#!/bin/bash
docker run --rm -v vpn-node_sing-box-data:/opt/sing-box alpine cat /opt/sing-box/config.json > /tmp/sb.json
python3 -c "
import json
d = json.load(open('/tmp/sb.json'))
print('OUTBOUNDS:', json.dumps(d.get('outbounds', []), indent=2))
print('DNS:', json.dumps(d.get('dns', {}), indent=2))
"
echo '---INET TEST---'
CNAME=$(docker ps --format '{{.Names}}' | grep -i sing)
echo "Container: $CNAME"
docker exec "$CNAME" wget -qO- --timeout=5 ifconfig.me 2>&1 || echo WGET_FAIL
