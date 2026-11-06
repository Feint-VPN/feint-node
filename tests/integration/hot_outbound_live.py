"""Exercise the real node runtime against one disposable HY2 relay, never production."""

import asyncio
import copy
import json
import re
import sys
from pathlib import Path

from adapters.docker_runtime import XrayDockerRuntime
from adapters.xray_socks import socks_outbound


async def main() -> None:
    setup = json.loads(sys.stdin.readline())
    name = setup["container"]
    if not re.fullmatch(r"feint-chain-[a-f0-9]{12}-relay", name):
        raise ValueError("Hot-swap acceptance may control only its disposable relay")
    path = Path(setup["path"])
    before = json.loads(await asyncio.to_thread(path.read_text))
    runtime = XrayDockerRuntime(
        str(path),
        str(path),
        runtime_config_path="/fixture/relay.json",
        container_name=name,
        api_listen=before["api"]["listen"],
        api_address=before["api"]["listen"],
    )
    native = copy.deepcopy(before)
    native["outbounds"][0] = socks_outbound(
        before["outbounds"][0]["tag"], setup["peer"]
    )
    native["routing"]["rules"][0]["outboundTag"] = native["outbounds"][0]["tag"]
    container = await asyncio.to_thread(runtime._get_container)
    identity = (
        container.id,
        container.attrs["State"]["StartedAt"],
        container.attrs["RestartCount"],
    )
    try:
        print(json.dumps({"controller": "ready"}), flush=True)
        for line in sys.stdin:
            variant = json.loads(line)["variant"]
            if variant not in {"native", "uot"}:
                raise ValueError("Expected native cutover or UoT rollback")
            candidate = native if variant == "native" else before
            assert candidate["inbounds"] == before["inbounds"]
            additions = runtime._additions(candidate)
            assert additions == [], "Transport-only switch must not mutate HY2 users"
            await asyncio.to_thread(path.write_text, json.dumps(candidate))
            result = await asyncio.to_thread(
                container.exec_run,
                ["xray", "run", "-test", "-c", "/fixture/relay.json"],
            )
            if result.exit_code:
                raise RuntimeError("Relay candidate rejected")
            await runtime._apply_hot(container, candidate, additions)
            await asyncio.to_thread(container.reload)
            assert identity == (
                container.id,
                container.attrs["State"]["StartedAt"],
                container.attrs["RestartCount"],
            ), "Disposable HY2 restarted during cutover"
            live = await runtime.control.outbound_tags()
            assert candidate["outbounds"][0]["tag"] in live
            print(
                json.dumps(
                    {
                        "variant": variant,
                        "core_restarted": False,
                        "hy2_credentials": "unchanged",
                        "resident_handlers": len(live),
                    }
                ),
                flush=True,
            )
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
