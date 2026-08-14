# Firewall setup

`setup-firewall.sh` configures UFW from the active `.env.local` file. Run it
from the project root after installation, or immediately after applying a port
change:

```bash
sudo ./scripts/setup-firewall.sh
```

The script opens:

- SSH (the current port, or a new port you choose interactively)
- TCP 80 for the Let's Encrypt standalone HTTP challenge
- The configured API TCP port
- VLESS, VMess, and Trojan TCP ports
- Hysteria2 UDP port
- Shadowsocks TCP and UDP ports

## Safe order for changing ports

First apply the running-service change, then update UFW:

```bash
bash scripts/ports.sh set --api 8337 --vless 28473 --apply
sudo ./scripts/setup-firewall.sh
```

Inspect the current configuration before making a change:

```bash
bash scripts/ports.sh show
bash scripts/ports.sh check
```

Do not open the new firewall ports before the port command has accepted the
configuration, and do not remove old rules manually unless you have verified
that the new listeners are healthy.

## Changing SSH

If prompted to change SSH, keep the current terminal open and verify a new
connection before closing it:

```bash
ssh -p 45123 user@your-server
```

If the new connection fails, use your VPS console to restore the latest
`/etc/ssh/sshd_config.backup.*`, restart SSH, and allow the previous port:

```bash
sudo systemctl restart sshd
sudo ufw allow 22/tcp
```

## Troubleshooting

```bash
sudo ufw status numbered
sudo ss -lntup
```

If UFW blocks access unexpectedly, use the server console:

```bash
sudo ufw disable
sudo ufw reset
sudo ufw allow 22/tcp
sudo ufw enable
```
