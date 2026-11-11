"""Host uptime probes."""

import time

from contracts.telemetry import HostTelemetry


class SystemTelemetry(HostTelemetry):
    def uptime_seconds(self) -> int | None:
        try:
            with open("/proc/uptime", encoding="utf-8") as uptime_file:
                return int(float(uptime_file.read().split()[0]))
        except (FileNotFoundError, ValueError, OSError):
            pass

        boot_time_epoch = SystemTelemetry._read_boot_time_epoch()
        if boot_time_epoch is None:
            return None

        return max(0, int(time.time() - boot_time_epoch))

    @staticmethod
    def _read_boot_time_epoch() -> int | None:
        try:
            with open("/proc/stat", encoding="utf-8") as stat_file:
                for line in stat_file:
                    if line.startswith("btime "):
                        return int(line.split()[1])
        except (FileNotFoundError, OSError, ValueError, IndexError):
            return None

        return None
