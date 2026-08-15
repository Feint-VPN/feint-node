"""Node-telemetry dependency."""

from threading import Lock

from adapters.node_telemetry import NodeTelemetryService
from adapters.singbox_file_store import SingBoxFileStore

node_telemetry_service: NodeTelemetryService | None = None
node_telemetry_service_lock = Lock()


def get_node_telemetry_service() -> NodeTelemetryService:
    global node_telemetry_service
    if node_telemetry_service is None:
        with node_telemetry_service_lock:
            if node_telemetry_service is None:
                node_telemetry_service = NodeTelemetryService(store=SingBoxFileStore())
    return node_telemetry_service
