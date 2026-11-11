"""Stage reviewed runtime dependencies for legacy API overlay destinations."""

import shutil
from pathlib import Path


def stage_support(source: Path, work: Path, *, services: bool = False) -> bool:
    if not (source / "runtime/docker.py").is_file():
        return False
    packages = ["runtime", "contracts", "models", "exceptions"]
    if services:
        packages += ["services", "store"]
    for package in packages:
        for path in (source / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            target = work / "src" / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for name in ("config.py", "log.py"):
        shutil.copyfile(source / name, work / "src" / name)
    return True


def write_bridge(work: Path, relative: str, content: str) -> None:
    target = work / "src" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def stage_runtime_bridges(work: Path, *, traffic: bool = False) -> None:
    write_bridge(
        work,
        "domain/errors.py",
        """from exceptions import (
    ConfigRollbackError, InboundNotFoundError, OutboundInUseError,
    OutboundNotFoundError, OutboundUserNotFoundError, ReverseTunnelError,
    SingBoxReloadError, UserAlreadyExistsError, UserNotFoundError,
)
""",
    )
    write_bridge(
        work,
        "adapters/docker_runtime.py",
        "from runtime.docker import DockerRuntime, NoopRuntime, XrayDockerRuntime\n",
    )
    write_bridge(
        work,
        "adapters/xray_control.py",
        "from runtime.xray.control import XrayControl, COMMAND, MESSAGES\n",
    )
    write_bridge(
        work,
        "adapters/xray_socks.py",
        "from runtime.xray.socks import socks_outbound\n",
    )
    if traffic:
        write_bridge(
            work,
            "adapters/tuic_runtime.py",
            "from runtime.tuic import TuicXrayRuntime, build_tuic_config\n",
        )
        write_bridge(
            work,
            "adapters/xray_config.py",
            """from runtime.xray.configuration import build_xray_config, render_xray_config
if __name__ == "__main__":
    import runpy
    runpy.run_module("runtime.xray.configuration", run_name="__main__")
""",
        )
        write_bridge(
            work,
            "adapters/traffic_tracker.py",
            """from runtime.v2ray import V2RayCounterSource, fetch_v2ray_user_counters, _QUERY_STATS_REQUEST, _QUERY_STATS_RESPONSE
from store.traffic import TrafficFileStore
from services.traffic import TrafficTracker as Tracker
class TrafficTracker(Tracker):
    def __init__(self, v2ray_api_address="127.0.0.1:10085", state_path="/opt/sing-box/traffic.json", additional_addresses=()):
        super().__init__(V2RayCounterSource(v2ray_api_address, additional_addresses), TrafficFileStore(state_path))
""",
        )
