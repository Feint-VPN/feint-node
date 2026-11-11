"""Node-telemetry dependency."""

from fastapi import Request
from services.telemetry import NodeTelemetryService


def get_node_telemetry_service(request: Request) -> NodeTelemetryService:
    return request.app.state.services.telemetry
