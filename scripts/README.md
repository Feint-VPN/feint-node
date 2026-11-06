# Node operator CLI

Use one entry point from any working directory:

```sh
python /opt/vpn-node/scripts/cli.py list
python /opt/vpn-node/scripts/cli.py list --json
python /opt/vpn-node/scripts/cli.py diagnose --help
```

Commands forward arguments, stdin and the child exit status without interpreting
credentials. Linux host commands still require Bash and their usual privileges.
Listing and help do not authorize executing an operation on production.

## Common operations

Run these examples from the checkout root:

```sh
python scripts/cli.py diagnose
python scripts/cli.py diagnose --logs 50
python scripts/cli.py ports show
python scripts/cli.py ports check
python scripts/cli.py ports set --api 8337 --vless 28473 --apply
python scripts/cli.py ports randomize --apply
python scripts/cli.py compose ps
python scripts/cli.py compose logs --tail=100 vpn-node-api
python scripts/cli.py compose --dev up --build
```

`ports set/randomize --apply`, `setup-ssh`, deployment and route commands change
host state. Obtain rollout authority and a verified backup before using them.
`subscription USER` prints connection credentials: do not save its output to
public logs. `diagnose` is read-only; logs are opt-in and bounded to 100 lines.

`compose` explicitly sets the project directory and env file to the installation
root. Do not invoke relocated YAML files without `--project-directory`: Docker
would otherwise derive different project/volume names and relative input paths.
Use `--dir PATH` before the Docker subcommand to select another installation.

## Implementations

| Directory | Commands |
| --- | --- |
| `lib/install/` | `install`, `setup-ssh` |
| `lib/update/` | `update` |
| `lib/runtime/` | `compose`, `ports`, `subscription`, `sync-config`, `udp-redirect`, hot-reload and TUIC overlays/canaries |
| `lib/interconnect/` | Interconnect configuration, management, preparation, cutover and rollback |
| `lib/diagnostics/` | Protocol probes, bounded WAN/chain tests and experimental route deployment |

Implementations may call one another internally. They are not additional public
entry points. Shared development stages remain in the sibling `pipeline`:

```sh
uv run --project ../pipeline python ../pipeline/scripts/context.py node
uv run --project ../pipeline python ../pipeline/scripts/stage.py node --run --fresh
```

Target-driven commands consume the pipeline inventory and explicit authority
flags. Do not replace inventory with hosts, IPs, keys or credentials in source.
`prepare-interconnect` is a remote worker with a target/files stdin envelope;
call it through `pipeline server-test`, not directly on an arbitrary host.

## Compatibility

Root `install.sh` remains a standalone Bash bootstrap and root `update.sh` remains
the installed updater entry point. The installer can run before Python exists.
The updater reselects old/new Compose paths after checkout and rollback.
Existing server files and systemd units are not changed by this repository move.

`update_tailscale.sh`, if present locally, is unrelated untracked work. It is not
registered, moved, committed or executed by this change.
