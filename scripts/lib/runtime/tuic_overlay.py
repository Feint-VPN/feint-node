"""Build a TUIC-only overlay of the installed API, retaining unrelated code.

Run on the Docker host with --source pointing at the reviewed node/src package.
This command builds an image; it does not restart or provision any service.
"""

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def replace_function(source: str, name: str, replacement: str) -> str:
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    lines = source.splitlines(keepends=True)
    prefix = " " * node.col_offset
    lines[node.lineno - 1 : node.end_lineno] = [
        prefix + line + "\n" for line in replacement.splitlines()
    ]
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--container", default="vpn-node-api")
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    inspect = json.loads(command("docker", "inspect", args.container))[0]
    image = json.loads(command("docker", "image", "inspect", inspect["Image"]))[0]
    base = next(iter(image.get("RepoDigests") or []), inspect["Image"])
    env = dict(item.split("=", 1) for item in inspect["Config"]["Env"])
    for relative in (
        "adapters/tuic_runtime.py",
        "adapters/docker_runtime.py",
        "adapters/xray_config.py",
        "adapters/xray_socks.py",
        "adapters/xray_control.py",
        "adapters/traffic_tracker.py",
    ):
        target = args.work / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source / relative, target)

    def read(relative: str) -> str:
        return command("docker", "exec", args.container, "cat", "/app/src/" + relative)

    def save(relative: str, source: str) -> None:
        ast.parse(source)
        target = args.work / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    source = read("domain/user_service.py")
    source = source.replace(
        'supported = {"vless", "hysteria2"}',
        'supported = {"vless", "hysteria2", "tuic"}',
        1,
    )
    source = source.replace(
        '"hysteria2": "hysteria2-in",',
        '"hysteria2": "hysteria2-in",\n    "tuic": "tuic-in",',
        1,
    )
    source = source.replace(
        '    raise ValueError(f"Unknown protocol: {proto}")',
        '    if proto == "tuic":\n        return InboundUser(name=username, uuid=uuid, password=password)\n    raise ValueError(f"Unknown protocol: {proto}")',
        1,
    )
    source = source.replace(
        '        if "shadowsocks" in by_proto:',
        """        if "tuic" in by_proto:
            user, ib = by_proto["tuic"]
            configs["tuic"] = {
                "protocol": "tuic",
                "config_url": self._url_builder.tuic_url(user.uuid or "", user.password or "", domain, ib.listen_port),
                "port": ib.listen_port,
            }

        if "shadowsocks" in by_proto:""",
        1,
    )
    assert '"tuic": "tuic-in"' in source and 'configs["tuic"]' in source
    save("domain/user_service.py", source)

    source = read("domain/models.py")
    for name in ("TLSConfig", "Inbound"):
        declaration = f"class {name}(BaseModel):\n"
        assert declaration in source
        if declaration + '    model_config = {"extra": "allow"}' not in source:
            source = source.replace(
                declaration,
                declaration + '    model_config = {"extra": "allow"}\n\n',
                1,
            )
    save("domain/models.py", source)

    local = (args.source / "adapters/url_builder.py").read_text(encoding="utf-8")
    method = next(
        n
        for n in ast.walk(ast.parse(local))
        if isinstance(n, ast.FunctionDef) and n.name == "tuic_url"
    )
    source = read("adapters/url_builder.py")
    source += (
        "\n\n"
        + "\n".join(
            "    " + line for line in ast.get_source_segment(local, method).splitlines()
        )
        + "\n"
    )
    # Older URL builder imports quote but not urlencode.
    source = "from urllib.parse import urlencode\n" + source
    save("adapters/url_builder.py", source)

    source = read("api/depends/runtime.py")
    source = source.replace(
        "from domain.ports import",
        "from adapters.tuic_runtime import TuicXrayRuntime\nfrom domain.ports import",
        1,
    )
    source = source.replace(
        "container_runtime = XrayDockerRuntime(",
        """options = {}
                    runtime_type = XrayDockerRuntime
                    if os.getenv("TUIC_CONTAINER_NAME"):
                        runtime_type = TuicXrayRuntime
                        options = {
                            "tuic_container_name": os.environ["TUIC_CONTAINER_NAME"],
                            "tuic_config_path": os.getenv("TUIC_CONFIG_PATH", "/opt/sing-box/tuic.json"),
                            "tuic_stats_listen": os.environ["TUIC_STATS_LISTEN"],
                        }
                    container_runtime = runtime_type(""",
        1,
    )
    source = source.replace(
        '                        api_listen=os.getenv("XRAY_API_LISTEN", "0.0.0.0:10085"),',
        """                        api_listen=os.getenv("XRAY_API_LISTEN", "0.0.0.0:10085"),
                        min_client_version=os.getenv("XRAY_REALITY_MIN_CLIENT_VERSION", ""),
                        hysteria2_compat_port=int(os.environ["HYSTERIA2_COMPAT_PORT"]) if os.getenv("HYSTERIA2_COMPAT_PORT") else None,
                        **options,""",
        1,
    )
    save("api/depends/runtime.py", source)

    source = "import os\n" + read("api/depends/statistics.py")
    source = source.replace(
        "TrafficTracker()",
        """TrafficTracker(
                    os.getenv("V2RAY_API_ADDRESS", "127.0.0.1:10085"),
                    os.getenv("TRAFFIC_STATE_PATH", "/opt/sing-box/traffic.json"),
                    additional_addresses=(os.environ["TUIC_STATS_ADDRESS"],) if os.getenv("TUIC_STATS_ADDRESS") else (),
                )""",
        1,
    )
    save("api/depends/statistics.py", source)
    for relative in ("api/routers/sub.py", "adapters/node_telemetry.py"):
        source = read(relative).replace(
            '"hysteria2": "Hysteria2",',
            '"hysteria2": "Hysteria2",\n    "tuic": "TUIC",',
            1,
        )
        if relative.endswith("sub.py"):
            source = source.replace(
                '"hysteria2", "shadowsocks"]', '"hysteria2", "tuic", "shadowsocks"]', 1
            )
        save(relative, source)
    (args.work / "Dockerfile").write_text(
        f"FROM {base}\nUSER root\nCOPY src/ /app/src/\nUSER 1000:1000\n",
        encoding="utf-8",
    )
    (args.work / "manifest.json").write_text(
        json.dumps(
            {
                "base_image": base,
                "container_user": inspect["Config"]["User"],
                "xray_api": env.get("XRAY_API_LISTEN"),
            }
        ),
        encoding="utf-8",
    )
    # Preserve the actual configured runtime user, including an empty root value.
    dockerfile = (args.work / "Dockerfile").read_text()
    dockerfile = dockerfile.replace(
        "USER 1000:1000", "USER " + (inspect["Config"]["User"] or "root")
    )
    (args.work / "Dockerfile").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "--network=none", "-t", args.image, str(args.work)],
        check=True,
    )
    print("Built TUIC overlay:", args.image)


if __name__ == "__main__":
    main()
