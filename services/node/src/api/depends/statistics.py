"""Traffic-statistics dependency."""

from adapters.traffic_tracker import TrafficTracker
from fastapi import Request


def get_traffic_tracker(request: Request) -> TrafficTracker:
    return request.app.state.services.tracker
