"""Probe node-local canaries from another host; forward credentials in RAM only."""

import argparse
import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--vantage", required=True)
    parser.add_argument("--jump-host")
    parser.add_argument("--user", action="append", required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--client", choices=("xray", "sing-box"), default="xray")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="One HTTPS and UDP smoke only, no sustained measurement",
    )
    parser.add_argument("--connect-host", type=ipaddress.IPv4Address)
    parser.add_argument("--connect-port", type=int)
    args = parser.parse_args()
    if (args.connect_host is None) != (args.connect_port is None) or (
        args.connect_port is not None and not 1024 <= args.connect_port <= 65535
    ):
        parser.error(
            "Diagnostic endpoint override requires an IPv4 address and port 1024..65535"
        )
    for host in (args.entry, args.vantage, args.jump_host):
        if host is not None and not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host
        ):
            parser.error("SSH hosts must be aliases without options")
    if not 1 <= len(args.user) <= 3 or any(
        not re.fullmatch(r"ops-hot-probe-[A-Za-z0-9_-]{1,40}", user)
        for user in args.user
    ):
        parser.error("Select 1..3 existing technical probe users")
    if not 15 <= args.seconds <= 180 or (args.seconds + 30) * len(args.user) > 550:
        parser.error("Probe duration exceeds the bounded 550s work budget")
    fetch = (
        "import os,ssl,urllib.request,json; "
        "base='https://127.0.0.1:'+os.environ['API_PORT']; "
        "headers={'X-API-Secret':os.environ['API_SECRET']}; "
        "domain=os.environ['SERVER_DOMAIN']; uris=[]; "
        "canonical=json.load(open('/opt/sing-box/config.json')); "
        "\nfor user in " + repr(args.user) + ":\n"
        " request=urllib.request.Request(base+'/user/'+user+'/configs?server_domain='+domain,headers=headers)\n"
        " with urllib.request.urlopen(request,context=ssl._create_unverified_context(),timeout=10) as response:\n"
        "  uri=json.load(response)['configs']['hysteria2']['config_url']\n"
        " routes=[r.get('outbound') for r in canonical['route']['rules'] if user in r.get('auth_user',[])]\n"
        " uris.append({'uri':uri,'user':user,'outbound':routes})\n"
        "print(json.dumps(uris))"
    )
    payload = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            args.entry,
            "docker exec vpn-node-api python -c " + shlex.quote(fetch),
        ],
        text=True,
        timeout=45,
    )
    uris = json.loads(payload)
    if args.connect_host is not None:
        for item in uris:
            parsed = urlsplit(item["uri"])
            authority = parsed.netloc.rsplit("@", 1)[0]
            item["uri"] = urlunsplit(
                parsed._replace(
                    netloc=f"{authority}@{args.connect_host}:{args.connect_port}"
                )
            )
            item["endpoint_override"] = {
                "host": str(args.connect_host),
                "port": args.connect_port,
            }
    source = Path(__file__).with_name("probe_protocols.py").read_text(encoding="utf-8")
    probe = (
        "import sys,json,tempfile,hashlib,signal\nfrom pathlib import Path\n"
        "p=json.load(sys.stdin)\n"
        "assert hashlib.sha256(p['source'].encode()).hexdigest()==p['sha256']\n"
        "def expire(*_): raise TimeoutError('Probe lease expired')\n"
        "signal.signal(signal.SIGALRM,expire); signal.alarm(600)\n"
        "with tempfile.TemporaryDirectory(prefix='feint-stability-') as directory:\n"
        " Path(directory,'probe_protocols.py').write_text(p['source'])\n"
        " sys.path.insert(0,directory)\n from probe_protocols import _probe\n"
        " results=[]\n"
        " for item in p['uris']:\n"
        "  image='ghcr.io/xtls/xray-core:26.7.28' if p['client']=='xray' else 'ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1'\n"
        "  result=_probe(item['uri'],image,8,p['seconds'],p['client'])\n"
        "  result.update(user=item['user'],outbound=item['outbound'])\n"
        "  if not p['seconds']: result['mode']='connect-only'\n"
        "  if 'endpoint_override' in item: result['endpoint_override']=item['endpoint_override']\n"
        "  results.append(result); print(json.dumps(result),flush=True)\n"
        " assert all(r['tcp'] and r['udp'] for r in results)\n"
        " if p['seconds']:\n"
        "  assert all('stability' in r for r in results)\n"
        "  assert all(r['stability']['udp_lost']==0 and not r['stability']['https_errors'] and not r['stability']['https_thread_alive'] for r in results)\n"
    )
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    if args.jump_host:
        command += ["-J", args.jump_host]
    result = subprocess.run(
        [*command, args.vantage, "python3 -u -c " + shlex.quote(probe)],
        input=json.dumps(
            {
                "uris": uris,
                "source": source,
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
                "seconds": 0 if args.smoke else args.seconds,
                "client": args.client,
            }
        ),
        stderr=subprocess.PIPE,
        text=True,
        timeout=620,
    )
    if result.returncode:
        raise RuntimeError(
            "Remote technical HY2 stability gate failed: " + result.stderr[-500:]
        )


if __name__ == "__main__":
    main()
