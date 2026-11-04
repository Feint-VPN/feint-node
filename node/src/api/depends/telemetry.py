"""Node-telemetry dependency."""

from adapters.node_telemetry import NodeTelemetryService
from fastapi import Request


def get_node_telemetry_service(request: Request) -> NodeTelemetryService:
    return request.app.state.services.telemetry
