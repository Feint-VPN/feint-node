"""Read-only deployment checks; failures keep the diagnostic API available."""

import os
from pathlib import Path

from utils.settings import Settings


def startup_errors(settings: Settings) -> list[str]:
    errors = []
    config = Path(settings.CONFIG_PATH)
    if not config.exists():
        errors.append(f"Config file not found at {config}")
    elif not config.is_file():
        errors.append(f"Config path {config} is not a file")
    elif not os.access(config, os.R_OK):
        errors.append(f"Config file {config} is not readable")
    socket = Path(settings.DOCKER_SOCKET)
    if not socket.exists():
        errors.append(f"Docker socket not found at {socket}")
    elif not socket.is_socket():
        errors.append(f"Docker socket path {socket} is not a socket")
    elif not os.access(socket, os.R_OK | os.W_OK):
        errors.append(f"Docker socket {socket} is not accessible (check permissions)")
    return errors
