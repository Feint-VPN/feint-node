"""Health and status HTTP presentation."""

import asyncio

from api.contract import API_VERSION
from api.depends import (
    get_container_runtime,
    get_node_telemetry_service,
    get_traffic_tracker,
    verify_api_secret,
)
from api.schemas.system import (
    NodeAvailability,
    NodeHealthResponse,
    NodeRuntimeStatus,
    NodeStatus,
    NodeStatusResponse,
)
from contracts import ContainerRuntime
from fastapi import APIRouter, Depends
from services.telemetry import NodeTelemetryService
from services.traffic import TrafficTracker

router = APIRouter()


@router.get("/health", tags=["health"], response_model=NodeHealthResponse)
async def health_check() -> NodeHealthResponse:
    """Return the cheap runtime health and API compatibility contract."""

    return NodeHealthResponse(status="ok", api_version=API_VERSION)


@router.get(
    "/status",
    tags=["health"],
    response_model=NodeStatusResponse,
    dependencies=[Depends(verify_api_secret)],
)
async def node_status(
    node_telemetry: NodeTelemetryService = Depends(get_node_telemetry_service),
    runtime: ContainerRuntime = Depends(get_container_runtime),
    tracker: TrafficTracker = Depends(get_traffic_tracker),
) -> NodeStatusResponse:
    telemetry, sing_box_running, statistics_available = await asyncio.gather(
        node_telemetry.get_status(),
        runtime.is_running(),
        tracker.is_available(),
    )
    configuration_available = telemetry.configuration_available
    healthy = configuration_available and sing_box_running and statistics_available
    return NodeStatusResponse(
        status=NodeStatus.OK if healthy else NodeStatus.DEGRADED,
        api_version=API_VERSION,
        uptime=telemetry.uptime,
        configuration=(
            NodeAvailability.AVAILABLE
            if configuration_available
            else NodeAvailability.UNAVAILABLE
        ),
        sing_box=(
            NodeRuntimeStatus.RUNNING if sing_box_running else NodeRuntimeStatus.STOPPED
        ),
        statistics=(
            NodeAvailability.AVAILABLE
            if statistics_available
            else NodeAvailability.UNAVAILABLE
        ),
        user_count=telemetry.user_count,
        protocols=telemetry.protocols,
    )
