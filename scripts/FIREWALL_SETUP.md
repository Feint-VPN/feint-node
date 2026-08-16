# Firewall setup

`setup-firewall.sh` configures UFW from the active `.env.local` file. Run it
from the project root after installation, or immediately after applying a port
change:

```bash
sudo ./scripts/setup-firewall.sh
```

The script always moves SSH to a new checked port and replaces the host's UFW
rules with this allowlist:

- The new SSH TCP port
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

All other host ports are denied. Docker-published bridge ports remain managed by
Docker rather than UFW.

## SSH confirmation

Keep the current terminal open. The old SSH port remains available only during
the transition. Open the command printed by the script in a second terminal:

```bash
ssh -p 45123 user@your-server
```

Type `CONFIRM` in the original terminal only after the second connection works.
The script then closes the old port. Any failure before confirmation restores
the SSH configuration and restrictive firewall on the previous SSH port.

Backups are retained under `/etc/ssh/feint-backups`. For console recovery:

```bash
sudo tar -C / -xpf /etc/ssh/feint-backups/sshd-<timestamp>.tar
sudo systemctl daemon-reload
sudo systemctl restart ssh.service
sudo ufw allow <old-port>/tcp
```

## Troubleshooting

```bash
sudo ufw status numbered
sudo ss -lntup
```

If UFW blocks access unexpectedly, use the server console and restore only the
required SSH port:

```bash
sudo ufw disable
sudo ufw reset
sudo ufw allow <old-port>/tcp
sudo ufw enable
```
