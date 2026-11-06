# Repository layout

Approved on 2026-11-06. This change reorganizes packaging and operational entry
points, not the node's internal application architecture or deployed servers.

- `services/`: independently built node API and VPN runtime components.
- `services/node/`: Python project, lockfile, source, templates and API contracts.
- `tests/`: cross-service integration, acceptance, CLI contracts and fixtures.
- `scripts/cli.py`: one operator CLI; implementation lives in `scripts/lib/`.
- `deploy/compose/`: Compose definitions; `deploy/systemd/`: host units.
- `install.sh` and `update.sh`: compatibility entry points only.
- The sibling `pipeline` owns shared development stages and evidence. Its node
  adapter invokes these paths; it does not duplicate node-specific operations.

Use an explicit repository project directory for Compose, preserving existing
project/volume names and resolving runtime inputs from the installation root.
Do not move private runtime configuration, certificates, caches or environments
into tracked service source. Preserve all current runtime implementations.

## Compatibility and verification

The SDK must receive its node-layout compatibility change before deploying this
checkout: lifecycle and interconnect commands prefer `scripts/cli.py` and retain
legacy fallbacks for older nodes. The pipeline node adapter uses the Python
environment under `services/node/.venv`; prepare it with
`uv sync --project services/node --locked --extra dev`.

The application's 52 source files and uv lockfile were moved without content
changes. Dependency-list consolidation is deliberately not mixed into this
packaging change: the existing Docker requirements remain unchanged.

Local verification covers static checks, service/CLI contracts, Compose path
resolution, Bash shims, disposable TCP/UDP/auth integrations and the built API
image's development-runtime HTTP scenario. Clean-host installation, ACME,
update/rollback across releases and public client VPN acceptance are separate
release gates; no production rollout is implied.
