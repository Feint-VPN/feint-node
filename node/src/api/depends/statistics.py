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
                traffic_tracker = TrafficTracker()
    return traffic_tracker
