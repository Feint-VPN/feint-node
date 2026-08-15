"""Container runtime status checks."""

from unittest.mock import MagicMock

import pytest
from adapters.docker_runtime import DockerRuntime, NoopRuntime
from docker.errors import NotFound


@pytest.mark.asyncio
async def test_docker_runtime_reports_running_container() -> None:
    container = MagicMock(status="running")
    client = MagicMock()
    client.containers.get.return_value = container

    assert await DockerRuntime(client=client).is_running()
    container.reload.assert_called_once()


@pytest.mark.asyncio
async def test_docker_runtime_reports_missing_container_as_stopped() -> None:
    client = MagicMock()
    client.containers.get.side_effect = NotFound("missing")

    assert not await DockerRuntime(client=client).is_running()


@pytest.mark.asyncio
async def test_noop_runtime_is_running() -> None:
    assert await NoopRuntime().is_running()
