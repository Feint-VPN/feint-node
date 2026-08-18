"""Outbound-service dependency."""

from threading import Lock

from api.depends.config import get_config_store, get_mutation_lock
from api.depends.runtime import get_container_runtime
from domain.outbound_service import OutboundService

outbound_service: OutboundService | None = None
outbound_service_lock = Lock()


def get_outbound_service() -> OutboundService:
    global outbound_service
    if outbound_service is None:
        with outbound_service_lock:
            if outbound_service is None:
                outbound_service = OutboundService(
                    store=get_config_store(),
                    runtime=get_container_runtime(),
                    mutation_lock=get_mutation_lock(),
                )
    return outbound_service
