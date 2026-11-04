"""Explicit test-host IPIP/SS2022 deployment; no production cutover or shared firewall edits."""

import base64
import csv
import ipaddress
import json
import os
import secrets
import shlex
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def run(*args, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-700:]}")
    return result.stdout.strip()


def configs(target, role, secret, *, core="sing-box"):
    network = ipaddress.ip_network(target["kernel_network"])
    address = str(network.network_address + (1 if role == "entry" else 2))
    peer = str(network.network_address + (2 if role == "entry" else 1))
    if core == "xray":
        if role == "exit":
            inbound = {
                "tag": "duck-transport",
                "protocol": "shadowsocks",
                "listen": address,
                "port": target["link_port"],
                "settings": {
                    "network": "tcp,udp",
                    "method": "2022-blake3-aes-128-gcm",
                    "password": secret,
                },
            }
            outbounds = [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ]
            rules = [{"type": "field", "ip": ["geoip:private"], "outboundTag": "block"}]
        else:
            inbound = {
                "tag": "duck-transport",
                "protocol": "socks",
                "listen": "127.0.0.1",
                "port": target["socks_port"],
                "settings": {"auth": "noauth", "udp": True},
            }
            outbounds = [
                {
                    "tag": "duck-wan",
                    "protocol": "shadowsocks",
                    "settings": {
                        "servers": [
                            {
                                "address": peer,
                                "port": target["link_port"],
                                "method": "2022-blake3-aes-128-gcm",
                                "password": secret,
                            }
                        ]
                    },
                }
            ]
            rules = [
                {
                    "type": "field",
                    "inboundTag": ["duck-transport"],
                    "outboundTag": "duck-wan",
                }
            ]
        return {
            "log": {"loglevel": "warning", "access": "none"},
            "inbounds": [inbound],
            "outbounds": outbounds,
            "routing": {"rules": rules},
        }
    if core != "sing-box":
        raise ValueError("Unsupported test route core")
    if role == "exit":
        return {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": "shadowsocks",
                    "listen": address,
                    "listen_port": target["link_port"],
                    "method": "2022-blake3-aes-128-gcm",
                    "password": secret,
                }
            ],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {
                "rules": [{"ip_is_private": True, "action": "reject"}],
                "final": "direct",
            },
        }
    return {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "socks",
                "listen": "127.0.0.1",
                "listen_port": target["socks_port"],
            }
        ],
        "outbounds": [
            {
                "type": "shadowsocks",
                "tag": "wan",
                "server": peer,
                "server_port": target["link_port"],
                "method": "2022-blake3-aes-128-gcm",
                "password": secret,
            }
        ],
        "route": {"final": "wan"},
    }


def host(payload):
    target, role = payload["target"], payload["role"]
    expected = target["entry_address"] if role == "entry" else target["exit_address"]
    if run("tailscale", "ip", "-4") != expected or os.geteuid() != 0:
        raise RuntimeError("Test-host identity mismatch")
    root = Path(target["directory"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    manifest = root / "deployment.json"
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if (
            any(target.get(key) != value for key, value in saved["target"].items())
            or saved["ss_password"] != payload["ss_password"]
        ):
            raise RuntimeError("Existing deployment differs; do not overwrite it")
        print(json.dumps({"status": "already-prepared", "role": role}))
        if role == "entry" and target.get("node_authorized"):
            deploy_node(target)
        return
    network = ipaddress.ip_network(target["kernel_network"])
    address = str(network.network_address + (1 if role == "entry" else 2))
    public = target["entry_public"] if role == "entry" else target["exit_public"]
    remote = target["exit_public"] if role == "entry" else target["entry_public"]
    interface = target["interface"]
    unit_path = Path("/etc/systemd/system") / (target["container_prefix"] + ".service")
    if unit_path.exists():
        raise RuntimeError("Test route unit already exists without a manifest")
    for item in json.loads(run("ip", "-json", "address", "show")):
        for info in item.get("addr_info", []):
            if info["family"] == "inet" and network.overlaps(
                ipaddress.ip_network(
                    f"{info['local']}/{info['prefixlen']}", strict=False
                )
            ):
                raise RuntimeError("The requested tunnel network is already in use")
    if json.loads(run("ip", "-json", "link", "show", "type", "ipip")):
        # The conventional unused tunl0 device is harmless; another active tunnel is not.
        links = json.loads(run("ip", "-json", "link", "show", "type", "ipip"))
        if any(item["ifname"] != "tunl0" for item in links):
            raise RuntimeError(
                "An IPIP tunnel already exists; inspect it before deployment"
            )
    run(
        "ip",
        "tunnel",
        "add",
        interface,
        "mode",
        "ipip",
        "local",
        public,
        "remote",
        remote,
        "ttl",
        "64",
    )
    started = False
    name = target["container_prefix"] + "-" + role
    try:
        run("ip", "address", "add", f"{address}/30", "dev", interface)
        run("ip", "link", "set", interface, "mtu", "1400", "up")
        run("ethtool", "-K", interface, "gro", "off", "gso", "off", "tso", "off")
        path = root / "transport.json"
        path.write_text(json.dumps(configs(target, role, payload["ss_password"])))
        path.chmod(0o600)
        image = target["singbox_image"]
        run("docker", "image", "inspect", image)
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{path}:/config.json:ro",
            image,
            "check",
            "-c",
            "/config.json",
        )
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "host",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--restart",
            "unless-stopped",
            "--memory",
            "128m",
            "--pids-limit",
            "64",
            "--log-opt",
            "max-size=5m",
            "--log-opt",
            "max-file=2",
            "-v",
            f"{path}:/config.json:ro",
            image,
            "run",
            "-c",
            "/config.json",
        )
        started = True
        unit = f"""[Unit]
Description=Feint isolated encrypted test-route underlay
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ip tunnel add {interface} mode ipip local {public} remote {remote} ttl 64
ExecStart=/usr/sbin/ip address add {address}/30 dev {interface}
ExecStart=/usr/sbin/ip link set {interface} mtu 1400 up
ExecStart=/usr/sbin/ethtool -K {interface} gro off gso off tso off
ExecStop=/usr/sbin/ip tunnel del {interface}
[Install]
WantedBy=multi-user.target
"""
        unit_path.write_text(unit)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", unit_path.name)
        # The running interface was prepared above; the unit is for subsequent boots.
        manifest.write_text(json.dumps(payload))
        manifest.chmod(0o600)
        print(
            json.dumps(
                {
                    "status": "prepared",
                    "role": role,
                    "address": address,
                    "container": name,
                }
            )
        )
        if role == "entry" and target.get("node_authorized"):
            deploy_node(target)
    except BaseException:
        if started:
            run("docker", "rm", "-f", name)
        if unit_path.exists():
            run("systemctl", "disable", unit_path.name)
            unit_path.unlink()
            run("systemctl", "daemon-reload")
        run("ip", "tunnel", "del", interface)
        raise


def deploy_node(target):
    """Install only the explicitly authorized experimental entry node."""
    root = Path(target["node_directory"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = root / "node-deployment.json"
    if marker.exists():
        if json.loads(marker.read_text()) != target:
            raise RuntimeError("Installed test node differs; inspect before updating")
        print(json.dumps({"node": "already-installed"}))
        return
    name = target["container_prefix"]
    for suffix in ("-hy2", "-api"):
        if (
            name + suffix
            in run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
        ):
            raise RuntimeError("Test node container exists without its manifest")
    for port, kind in (
        (target["hy2_port"], socket.SOCK_DGRAM),
        (target["node_api_port"], socket.SOCK_STREAM),
        (10085, socket.SOCK_STREAM),
        (80, socket.SOCK_STREAM),
    ):
        with socket.socket(socket.AF_INET, kind) as probe:
            probe.bind(("0.0.0.0", port))
    certs = root / "certificates"
    certs.mkdir(mode=0o700, exist_ok=True)
    domain = target["domain"]
    cert = certs / "live" / domain / "fullchain.pem"
    certbot = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-v",
        f"{certs}:/etc/letsencrypt",
        target["certbot_image"],
    ]
    if not cert.exists():
        run(
            *certbot,
            "certonly",
            "--standalone",
            "--non-interactive",
            "--agree-tos",
            "--email",
            target["certificate_email"],
            "--cert-name",
            domain,
            "-d",
            domain,
            timeout=180,
        )
    run("openssl", "x509", "-in", str(cert), "-noout", "-checkend", "604800")
    run("openssl", "x509", "-in", str(cert), "-noout", "-checkhost", domain)
    # The unprivileged API/runtime share only this certificate's read access.
    for path in [
        certs,
        certs / "live",
        certs / "archive",
        certs / "live" / domain,
        certs / "archive" / domain,
    ]:
        os.chown(path, 0, 1000)
        path.chmod(0o750)
    for path in (certs / "archive" / domain).iterdir():
        os.chown(path, 0, 1000)
        path.chmod(0o640)
    config_root = root / "data"
    config_root.mkdir(mode=0o750, exist_ok=True)
    os.chown(config_root, 1000, 1000)
    config = {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            {
                "type": "hysteria2",
                "tag": "hysteria2-in",
                "listen": "0.0.0.0",
                "listen_port": target["hy2_port"],
                "users": [],
                "tls": {
                    "enabled": True,
                    "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                    "key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
                },
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {
            "rules": [{"ip_is_private": True, "action": "reject"}],
            "final": "direct",
        },
        "experimental": {
            "v2ray_api": {
                "listen": "127.0.0.1:10085",
                "stats": {"enabled": True, "users": []},
            }
        },
    }
    path = config_root / "config.json"
    path.write_text(json.dumps(config))
    path.chmod(0o640)
    os.chown(path, 1000, 1000)
    env = root / ".env.local"
    settings = {
        "API_SECRET": secrets.token_urlsafe(48),
        "SERVER_DOMAIN": domain,
        "HYSTERIA2_PORT": str(target["hy2_port"]),
        "HYSTERIA2_PUBLIC_PORT": str(target["hy2_port"]),
        "PUBLISHED_PROTOCOLS": "hysteria2",
        "VPN_RUNTIME": "sing-box",
        "VPN_RUNTIME_CONTAINER_NAME": name + "-hy2",
        "SINGBOX_CONTAINER_NAME": name + "-hy2",
        "CONFIG_PATH": "/opt/sing-box/config.json",
        "BACKUP_DIR": "/opt/sing-box/backups",
        "TRAFFIC_STATE_PATH": "/opt/sing-box/traffic.json",
        "V2RAY_API_ADDRESS": "127.0.0.1:10085",
        "API_PORT": str(target["node_api_port"]),
        "HIDE_ENDPOINTS": "true",
        "DEV_MODE": "false",
        "SUBSCRIPTION_ENABLED": "false",
        "LOG_LEVEL": "warning",
    }
    env.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    env.chmod(0o600)
    os.chown(env, 1000, 1000)
    mounts = [
        "-v",
        f"{config_root}:/opt/sing-box",
        "-v",
        f"{certs}:/etc/letsencrypt:ro",
    ]
    image = target["singbox_image"]
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        *mounts,
        image,
        "check",
        "-c",
        "/opt/sing-box/config.json",
    )
    common = [
        "--network",
        "host",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--restart",
        "unless-stopped",
        "--pids-limit",
        "128",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
    ]
    started = []
    try:
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name + "-hy2",
            *common,
            "--user",
            "0:1000",
            "--cap-add",
            "NET_BIND_SERVICE",
            "--read-only",
            "--memory",
            "384m",
            *mounts,
            image,
            "run",
            "-c",
            "/opt/sing-box/config.json",
        )
        started.append(name + "-hy2")
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name + "-api",
            *common,
            "--memory",
            "384m",
            "--group-add",
            str(os.stat("/var/run/docker.sock").st_gid),
            "--env-file",
            str(env),
            *mounts,
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            f"{env}:/opt/vpn-node/.env.local",
            "--entrypoint",
            "uvicorn",
            target["node_image"],
            "main:app",
            "--host",
            target["entry_address"],
            "--port",
            str(target["node_api_port"]),
            "--ssl-certfile",
            f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "--ssl-keyfile",
            f"/etc/letsencrypt/live/{domain}/privkey.pem",
        )
        started.append(name + "-api")
        # Renewal is short-lived, and only these two node containers may reload.
        renewal = f"""import hashlib,os,subprocess\nfrom pathlib import Path\nroot=Path({str(certs)!r})\ncert=root/'live'/{domain!r}/'fullchain.pem'\nbefore=hashlib.sha256(cert.read_bytes()).digest()\nsubprocess.run({[*certbot, "renew", "--cert-name", domain, "--quiet"]!r},check=True,timeout=180)\nfor p in (root/'archive'/{domain!r}).iterdir():\n os.chown(p,0,1000);p.chmod(0o640)\nif hashlib.sha256(cert.read_bytes()).digest()!=before:\n subprocess.run(['docker','restart',{name + "-hy2"!r},{name + "-api"!r}],check=True,timeout=60)\n"""
        renewal_path = root / "renew.py"
        renewal_path.write_text(renewal)
        renewal_path.chmod(0o600)
        unit = name + "-certificate"
        Path(f"/etc/systemd/system/{unit}.service").write_text(
            f"[Unit]\nDescription=Renew experimental Duck node certificate\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {renewal_path}\n"
        )
        Path(f"/etc/systemd/system/{unit}.timer").write_text(
            "[Unit]\nDescription=Experimental Duck certificate renewal\n[Timer]\nOnCalendar=*-*-* 03,15:00:00\nRandomizedDelaySec=30m\nPersistent=true\n[Install]\nWantedBy=timers.target\n"
        )
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", unit + ".timer")
        time.sleep(3)
        for container in started:
            if (
                run(
                    "docker",
                    "inspect",
                    container,
                    "--format",
                    "{{.State.Running}} {{.RestartCount}}",
                )
                != "true 0"
            ):
                raise RuntimeError("The new test node did not remain running")
        context = ssl.create_default_context()
        context.check_hostname = (
            False  # Private address; certificate host was checked above.
        )
        request = urllib.request.Request(
            f"https://{target['entry_address']}:{target['node_api_port']}/health",
            headers={"X-API-Secret": settings["API_SECRET"]},
        )
        with urllib.request.urlopen(request, context=context, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("The test node API is not healthy")
        marker.write_text(json.dumps(target))
        marker.chmod(0o600)
    except BaseException:
        for container in reversed(started):
            run("docker", "rm", "-f", container)
        raise
    print(
        json.dumps(
            {
                "node": "installed",
                "protocol": "hysteria2",
                "api_private": target["entry_address"],
                "api_port": target["node_api_port"],
            }
        )
    )


def validate(target):
    if (
        target.get("test_only") is not True
        or target.get("apply_authorized") is not True
    ):
        raise ValueError("An explicit test-host deployment authorization is required")
    for key in ("entry_public", "exit_public", "entry_address", "exit_address"):
        ipaddress.IPv4Address(target[key])
    network = ipaddress.ip_network(target["kernel_network"])
    if network.prefixlen != 30 or not network.is_private:
        raise ValueError("Use an unused private /30 for the isolated tunnel")
    if not target["singbox_image"].startswith(
        "ghcr.io/feint-vpn/feint-sing-box@sha256:"
    ):
        raise ValueError("Pin the published Feint sing-box image")
    for key in ("link_port", "socks_port"):
        if type(target[key]) is not int or not 1024 <= target[key] <= 65535:
            raise ValueError("Invalid test port")
    for key in ("interface", "container_prefix"):
        if not target[key].replace("-", "").isalnum() or len(target[key]) > 15:
            raise ValueError("Unsafe resource name")
    if not target["directory"].startswith("/opt/feint-") or ".." in target["directory"]:
        raise ValueError("Use a dedicated /opt/feint-* deployment directory")
    if target.get("node_authorized"):
        for key in ("node_image", "certbot_image"):
            if "@sha256:" not in target[key]:
                raise ValueError("Pin all node images by registry digest")
        if (
            not target["node_directory"].startswith("/opt/feint-")
            or ".." in target["node_directory"]
        ):
            raise ValueError("Use a dedicated test node directory")
        for key in ("hy2_port", "node_api_port"):
            if type(target[key]) is not int or not 1 <= target[key] <= 65535:
                raise ValueError("Invalid node listener port")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "host":
        host(json.loads(sys.stdin.readline()))
        return
    target = json.loads(sys.stdin.read())
    validate(target)
    if target["entry_public"] not in {
        item[4][0]
        for item in socket.getaddrinfo(target["domain"], None, socket.AF_INET)
    }:
        raise RuntimeError("Entry DNS does not match its public IP")
    key = Path(target["secret_file"])
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(base64.b64encode(secrets.token_bytes(16)).decode())
        if os.name == "nt":
            sid = next(csv.reader([run("whoami", "/user", "/fo", "csv", "/nh")]))[1]
            run(
                "icacls",
                str(key),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(F)",
                "*S-1-5-18:(F)",
            )
    secret = key.read_text().strip()
    source = Path(__file__).read_text()
    for role in ("exit", "entry"):
        ssh = target["ssh"] if role == "exit" else target["entry_ssh"]
        command = "python3 -u -c " + shlex.quote(
            'import json,sys; s=json.loads(sys.stdin.readline())["source"]; sys.argv=["runner","host"]; exec(s)'
        )
        gateway = target.get("gateway")
        if gateway and ssh != gateway:
            command = "tailscale ssh " + shlex.quote(ssh) + " " + shlex.quote(command)
            ssh = gateway
        result = subprocess.run(
            ["tailscale", "ssh", ssh, command],
            text=True,
            capture_output=True,
            input=json.dumps({"source": source})
            + "\n"
            + json.dumps({"target": target, "role": role, "ss_password": secret})
            + "\n",
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError(
                f"Test route {role} failed; inspect only its dedicated resources: {result.stderr[-1200:]}"
            )
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
