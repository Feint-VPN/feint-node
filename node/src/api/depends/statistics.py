import os
"""Traffic-statistics dependencies."""

from threading import Lock

from adapters.traffic_tracker import TrafficTracker

traffic_tracker: TrafficTracker | None = None
traffic_tracker_lock = Lock()


def get_traffic_tracker() -> TrafficTracker:
    global traffic_tracker
    if traffic_tracker is None:
        with traffic_tracker_lock:
            if traffic_tracker is None:
                traffic_tracker = TrafficTracker(
                    os.getenv("V2RAY_API_ADDRESS", "127.0.0.1:10085"),
                    os.getenv("TRAFFIC_STATE_PATH", "/opt/sing-box/traffic.json"),
                    additional_addresses=(os.environ["TUIC_STATS_ADDRESS"],) if os.getenv("TUIC_STATS_ADDRESS") else (),
                )
    return traffic_tracker
