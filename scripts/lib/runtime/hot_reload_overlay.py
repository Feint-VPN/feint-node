"""Build a scoped Xray hot-registration API overlay; never restart services."""

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path

from overlay_sources import stage_runtime_bridges, stage_support


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--container", default="vpn-node-api")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    inspect = json.loads(command("docker", "inspect", args.container))[0]
    base = args.image + "-base"
    command("docker", "tag", inspect["Image"], base)

    def read(relative: str) -> str:
        return command("docker", "exec", args.container, "cat", "/app/src/" + relative)

    def save(relative: str, source: str) -> None:
        ast.parse(source)
        target = args.work / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    layered = stage_support(args.source, args.work)
    if layered:
        stage_runtime_bridges(args.work)
    else:
        for relative in ("adapters/docker_runtime.py", "adapters/xray_control.py"):
            save(relative, (args.source / relative).read_text(encoding="utf-8"))

    source = read("adapters/xray_config.py")
    signature = "    hysteria2_compat_port: int | None = None,"
    if source.count(signature) == 2:
        source = source.replace(
            signature, signature + "\n    control_api: bool = False,"
        )
    elif (
        "def build_xray_config" not in source
        and source.count('    api_listen: str = "0.0.0.0:10085",') == 1
    ):
        source = source.replace(
            '    api_listen: str = "0.0.0.0:10085",',
            '    api_listen: str = "0.0.0.0:10085",\n    *,\n    min_client_version: str = "",\n    hysteria2_compat_port: int | None = None,\n    control_api: bool = False,',
        )
        source = source.replace(
            "    config = SingBoxConfig.model_validate_json",
            '    if min_client_version or hysteria2_compat_port:\n        raise ValueError("Legacy renderer does not support compatibility overrides")\n    config = SingBoxConfig.model_validate_json',
        )
        source = source.replace(
            '        sys.argv[3] if len(sys.argv) > 3 else "0.0.0.0:10085",',
            '        sys.argv[3] if len(sys.argv) > 3 else "0.0.0.0:10085",\n        control_api=os.getenv("XRAY_HOT_RELOAD", "false").lower() == "true",',
        )
    else:
        raise ValueError("Unexpected installed renderer signatures")
    source = source.replace(
        '"services": ["StatsService"],',
        '"services": ["StatsService", "HandlerService", "RoutingService"] if control_api else ["StatsService"],',
    )
    source = source.replace(
        "        hysteria2_compat_port=hysteria2_compat_port,",
        "        hysteria2_compat_port=hysteria2_compat_port,\n        control_api=control_api,",
    )
    source = source.replace(
        "        hysteria2_compat_port=settings.HYSTERIA2_COMPAT_PORT,",
        '        hysteria2_compat_port=settings.HYSTERIA2_COMPAT_PORT,\n        control_api=os.getenv("XRAY_HOT_RELOAD", "false").lower() == "true",',
    )
    # The installed CLI predates the Settings attributes used by its renderer.
    source = source.replace(
        "settings.XRAY_API_LISTEN", 'os.getenv("XRAY_API_LISTEN", "0.0.0.0:10085")'
    )
    source = source.replace(
        "settings.XRAY_REALITY_MIN_CLIENT_VERSION",
        'os.getenv("XRAY_REALITY_MIN_CLIENT_VERSION", "")',
    )
    source = source.replace(
        "settings.HYSTERIA2_COMPAT_PORT",
        'int(os.environ["HYSTERIA2_COMPAT_PORT"]) if os.getenv("HYSTERIA2_COMPAT_PORT") else None',
    )
    save("adapters/xray_config.py", source)

    runtime = read("api/depends/runtime.py")
    line = 'api_listen=os.getenv("XRAY_API_LISTEN", "0.0.0.0:10085"),'
    if runtime.count(line) != 1:
        raise ValueError("Unexpected installed runtime composition")
    runtime = runtime.replace(
        line,
        line
        + '\n                        api_address=os.getenv("V2RAY_API_ADDRESS", "127.0.0.1:10085") if os.getenv("XRAY_HOT_RELOAD", "false").lower() == "true" else None,',
    )
    save("api/depends/runtime.py", runtime)
    if "TuicXrayRuntime" in runtime:
        source = read("adapters/tuic_runtime.py")
        source = source.replace(
            "            hysteria2_compat_port=self.hysteria2_compat_port,",
            "            hysteria2_compat_port=self.hysteria2_compat_port,\n            control_api=self.control is not None,",
        )
        save("adapters/tuic_runtime.py", source)

    dockerfile = args.work / "Dockerfile"
    dockerfile.write_text(
        f"FROM {base}\nUSER root\nCOPY src/ /app/src/\nRUN python -m compileall -q /app/src\nUSER 1000:1000\n",
        encoding="utf-8",
    )
    shutil.copyfile(
        args.source
        / ("runtime/xray/control.py" if layered else "adapters/xray_control.py"),
        args.work / "src/adapters/xray_control.py",
    )
    command("docker", "build", "-t", args.image, str(args.work))
    print(
        json.dumps(
            {
                "image": args.image,
                "base_image": inspect["Image"],
                "work": str(args.work),
            }
        )
    )


if __name__ == "__main__":
    main()
