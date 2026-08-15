"""Traffic-statistics dependencies."""

from threading import Lock

from adapters.clash_stats import ClashStatsBackend
from adapters.traffic_tracker import TrafficTracker

stats_backend: ClashStatsBackend | None = None
traffic_tracker: TrafficTracker | None = None
stats_backend_lock = Lock()
traffic_tracker_lock = Lock()


def get_stats_backend() -> ClashStatsBackend:
    global stats_backend
    if stats_backend is None:
        with stats_backend_lock:
            if stats_backend is None:
                stats_backend = ClashStatsBackend()
    return stats_backend


def get_traffic_tracker() -> TrafficTracker:
    global traffic_tracker
    if traffic_tracker is None:
        with traffic_tracker_lock:
            if traffic_tracker is None:
                traffic_tracker = TrafficTracker()
    return traffic_tracker
