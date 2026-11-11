"""Traffic-statistics dependency."""

from fastapi import Request
from services.traffic import TrafficTracker


def get_traffic_tracker(request: Request) -> TrafficTracker:
    return request.app.state.services.tracker
