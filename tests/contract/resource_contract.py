"""Resource accounting includes host overhead without double counting guest ticks."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/integration"))
from interconnect_load import resource_delta, resource_snapshot


class ResourceContract(unittest.TestCase):
    def test_real_proc_and_cgroup_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc, groups = root / "proc", root / "cgroup"
            (proc / "123").mkdir(parents=True)
            (groups / "canary").mkdir(parents=True)
            (proc / "stat").write_text("cpu 100 0 100 700 50 10 30 10 80 0\n")
            (proc / "meminfo").write_text("MemAvailable: 1024 kB\n")
            fields = ["0"] * 22
            fields[0], fields[11], fields[12] = "S", "70", "30"
            fields[19], fields[21] = "555", "10"
            (proc / "123/stat").write_text("123 (tailscaled) " + " ".join(fields))
            (proc / "123/cgroup").write_text("0::/canary\n")
            (groups / "canary/cpu.stat").write_text(
                "usage_usec 1000000\nthrottled_usec 0\nnr_throttled 0\n"
            )
            (groups / "canary/memory.current").write_text("4096")
            with (
                patch("interconnect_load.sys.platform", "linux"),
                patch(
                    "interconnect_load.subprocess.check_output", return_value="123\n"
                ),
                patch(
                    "interconnect_load.os.sysconf",
                    create=True,
                    side_effect=lambda key: {"SC_CLK_TCK": 100, "SC_PAGE_SIZE": 4096}[
                        key
                    ],
                ),
            ):
                before = resource_snapshot({"relay": "fixture"}, proc, groups)
            self.assertEqual(len(before["host_ticks"]), 8)
            self.assertEqual(before["host_available_bytes"], 1048576)
            self.assertEqual(before["tailscale"]["123:555"]["rss_bytes"], 40960)
            self.assertIsNone(before["containers"]["relay"]["memory_peak_bytes"])
            after = copy.deepcopy(before)
            after["host_ticks"] = [150, 0, 150, 780, 60, 10, 60, 10]
            after["tailscale"]["123:555"]["ticks"] += 40
            after["containers"]["relay"].update(
                usage_usec=1600000, throttled_usec=100000, nr_throttled=4
            )
            result = resource_delta(before, after, 2)
            self.assertEqual(result["host_cpu_percent"], 59.09)
            self.assertEqual(result["host_softirq_percent_one_core"], 15)
            self.assertEqual(result["tailscale_cpu_percent_one_core"], 20)
            self.assertEqual(result["containers"]["relay"]["cpu_percent_one_core"], 30)
            self.assertEqual(result["containers"]["relay"]["throttled_seconds"], 0.1)
            self.assertEqual(result["containers"]["relay"]["throttled_periods"], 4)
            after["tailscale"] = {"123:999": {"ticks": 10000, "rss_bytes": 100}}
            changed = resource_delta(before, after, 2)
            self.assertTrue(changed["tailscale_process_set_changed"])
            self.assertIsNone(changed["tailscale_cpu_percent_one_core"])

    def test_unsupported_platform_reports_unknown_not_zero_cpu(self) -> None:
        with patch("interconnect_load.sys.platform", "win32"):
            snapshot = resource_snapshot({})
        result = resource_delta(snapshot, snapshot, 1)
        self.assertIsNone(result["host_cpu_percent"])
        self.assertIsNone(result["tailscale_cpu_percent_one_core"])
        self.assertFalse(result["containers"])


if __name__ == "__main__":
    unittest.main()
