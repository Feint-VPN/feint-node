"""Node-telemetry dependency."""

from threading import Lock

from adapters.node_telemetry import NodeTelemetryService
from api.depends.config import get_config_store

node_telemetry_service: NodeTelemetryService | None = None
node_telemetry_service_lock = Lock()


def get_node_telemetry_service() -> NodeTelemetryService:
    global node_telemetry_service
    if node_telemetry_service is None:
        with node_telemetry_service_lock:
            if node_telemetry_service is None:
                node_telemetry_service = NodeTelemetryService(store=get_config_store())
    return node_telemetry_service
