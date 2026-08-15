# Feint Node Runtime 👾

<p align="center">
  <img src="../static/banner.png" alt="Feint Node banner">
</p>

> 🌙 The Python runtime inside one Feint node.

This directory contains the FastAPI application, its active contracts and the
production Docker image. The complete installation, security, endpoint and
operations guide lives in the repository [README](../README.md).

## ✨ Runtime path

```text
main.py
  └── api/          FastAPI routers, dependencies and Pydantic schemas
        └── domain/ Local user behavior and infrastructure contracts
              └── adapters/ sing-box files, Docker, traffic and URL generation
```

There is one implementation path. The retired parallel `routers`, `schemas`,
`services`, `interfaces` and `models` trees no longer exist.

## 🪐 Packages

| Package | Responsibility |
| --- | --- |
| `src/api` | HTTP routes, authentication dependencies and request/response schemas. |
| `src/domain` | Local user lifecycle, protocol mapping, errors and adapter contracts. |
| `src/adapters` | Atomic configuration storage, Docker runtime, telemetry, traffic and URL building. |
| `src/utils` | Settings, cryptographic values and structured secret-safe logging. |
| `tests` | Unit, integration, property and deployment-script regression tests. |

## 🌙 Development

Create the environment from the repository root:

```powershell
uv sync --locked --extra dev
```

Run the complete runtime suite:

```powershell
cd node
uv run pytest -q --no-cov
```

Run formatting and lint checks from the root:

```powershell
uv run ruff format --check node/src node/tests
uv run ruff check node/src node/tests
```

## 🛸 Container

The production image installs only `requirements.txt` and starts
`uvicorn main:app`. Development-only packages are isolated in
`requirements-dev.txt` and the `dev` uv extra.

```bash
docker build -t feint-node ./node
```

The image runs as `appuser`, reads `/opt/sing-box/config.json`, stores backups
under `/opt/sing-box/backups` and controls the `sing-box` container through the
mounted Docker socket.

## 🔮 Contract

The public runtime contract is versioned independently through
`api.contract.API_VERSION`. The maintainer uses:

- `/health` for a cheap compatibility probe;
- `/status` for config, sing-box and statistics readiness plus node telemetry;
- `/user` and `/users` for local user lifecycle;
- `/stats` for local traffic counters;
- optional `/sub/{username}` for one-node subscription output.

There is intentionally no root endpoint. Administrative requests use
`X-API-Secret`, and production endpoint hiding makes protected and unknown
paths indistinguishable.
