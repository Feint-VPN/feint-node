# Node internal architecture

Approved direction: the November 11, 2026 owner discussion. This document defines
the internal source layout; verification evidence belongs to the execution plan.

## Scope

Refactor `services/node/src` into explicit application, service, contract,
model, persistence, runtime and HTTP boundaries. Do not introduce a surrounding
`feint_node` package. Python packages expose deliberate names through `__init__.py`.
Use `contracts` and `models`, not top-level `abc` and `types`, which conflict with
Python's standard library. This is a layered service design, not vertical slices
or a literal three-directory MVC rewrite. No architecture skill is installed.

## Ownership

| Location below src | Responsibility |
| --- | --- |
| `main.py` | FastAPI creation, middleware and route registration |
| `application.py` | Composition and ownership of services, shared locks and lifecycle |
| `config.py` | Validated startup settings; no settings lookup by inner services |
| `log.py` | Logging configuration and existing redaction behavior |
| `contracts/` | Interfaces at actual persistence, runtime and counter boundaries |
| `models/` | Internal state, validated operation inputs and service results |
| `exceptions/` | Existing application exceptions, independent of HTTP |
| `store/` | Files, atomic writes, backup/restore and persisted checkpoints |
| `runtime/` | Docker, VPN core control/rendering, gRPC and host telemetry |
| `services/` | User/outbound changes, traffic accounting, subscriptions and reverse workflows |
| `api/` | HTTP inputs, authentication, thin handlers and response serialization |

HTTP schemas remain separate from internal models where they enforce a distinct
external contract. Do not duplicate identical types solely to fill directories.
`SingBoxConfig` keeps its current name and shape: it is not made core-neutral by
renaming a file.

## Dependency rules

- Models and exceptions do not import services, infrastructure, FastAPI or settings.
- Contracts may refer to models and exceptions, not concrete implementations.
- Services use contracts and internal models; infrastructure clients stay hidden.
- Store/runtime implement the contracts and do not import API or application services.
- API obtains application-owned services, never creates a Docker client/tracker per request.
- Application composition is the only place wiring concrete store/runtime into services.
- Public `__init__.py` exports are explicit; no star imports, connection opening,
  startup validation, background tasks or logging configuration on package import.
- Do not add generic managers, repositories, event buses or class-per-function wrappers.

## Main decompositions

- Move `domain.user_service` and `domain.outbound_service` into services intact first.
- Keep one shared config mutation lock. Preserve backup, apply, cancellation and
  rollback order; do not quietly change cold-restart or user-revocation semantics.
- Extract the traffic tracker into counter I/O (`runtime/v2ray.py`), accumulation
  and poll ownership (`services/traffic.py`), and checkpoint I/O (`store/traffic.py`).
- Extract subscription label/content rules from the HTTP router; persisted label
  writes belong to store, not the process configuration object.
- Separate reverse workflow from Rathole process/file details without changing
  accepted configurations, public keys, secret treatment or rollback behavior.
- Move health/status assembly out of `main.py`; preserve existing paths and responses.

## Compatibility

Retain public HTTP routes, status codes, subscription ordering and label encoding,
user credentials, model extra-field preservation, environment names/defaults,
configuration paths, Compose project/volume identities and all current runtimes.
Update test imports, current CLI invocations and source-bundle allowlists together.
Historical overlay scripts may target the old deployed layout: distinguish their
new local source paths from their legacy destination paths instead of replacing
every `adapters` string. No old server/image is upgraded in this task.

## Verification

Use the workspace pipeline for baseline and final stages. Add behavioral checks
for package-import side effects, shared-lock concurrency, traffic checkpoint
recovery, route/subscription responses and lifecycle cleanup. Test real file
state and loopback gRPC where those boundaries are being separated.
Docker acceptance uses isolated local resources only. Successful local checks
do not establish public HY2, real Discord, installer/ACME or production acceptance.

## Boundaries

Keep the existing feature branch and unrelated untracked Tailscale updater.
No worktrees, main/dev integration, server changes, user-data operations, new
dependencies or transport-policy tuning are implied. The owner subsequently
authorized local commits and pushing the reviewed changes to the feature branch.
Do not mix removal of supported sing-box/TUIC/Rathole with the directory migration.
