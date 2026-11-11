"""Injected counter sources must preserve file-backed totals across core restarts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class TrafficLayersContract(unittest.IsolatedAsyncioTestCase):
    async def test_two_sources_reset_and_restart_do_not_double_count(self):
        from contracts.statistics import CounterSource
        from services.traffic import TrafficTracker
        from store.traffic import TrafficFileStore

        class Source(CounterSource):
            addresses = ("xray:1", "tuic:2")
            values = ((100, 200), (10, 20))

            async def start(self):
                pass

            async def close(self):
                pass

            async def read(self):
                return [
                    (address, {"alice": {"upload": up, "download": down}})
                    for address, (up, down) in zip(
                        self.addresses, self.values, strict=True
                    )
                ]

        with tempfile.TemporaryDirectory() as temporary:
            store = TrafficFileStore(str(Path(temporary) / "traffic.json"))
            source = Source()
            tracker = TrafficTracker(source, store)
            await tracker._poll_once()
            await tracker._poll_once()
            source.values = ((3, 7), (12, 23))
            await tracker._poll_once()
            await tracker._flush_state(force=True)
            saved = json.loads(store.path.read_text())
            self.assertEqual(saved["users"]["alice"]["upload"], 115)
            self.assertEqual(saved["users"]["alice"]["download"], 230)
            self.assertEqual(saved["counters"]["xray:1"]["alice"], [3, 7])

            restored = TrafficTracker(source, store)
            await restored._load_state()
            await restored._poll_once()
            result = await restored.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (115, 230))

    async def test_corrupt_checkpoint_is_not_treated_as_live_totals(self):
        from runtime.v2ray import V2RayCounterSource
        from services.traffic import TrafficTracker
        from store.traffic import TrafficFileStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "traffic.json"
            path.write_text("not valid json")
            tracker = TrafficTracker(
                V2RayCounterSource("127.0.0.1:1"), TrafficFileStore(str(path))
            )
            await tracker._load_state()
            self.assertEqual(await tracker.get_all(), {})
            self.assertFalse(await tracker.is_available())


if __name__ == "__main__":
    unittest.main()
