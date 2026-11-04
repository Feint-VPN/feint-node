"""Outbound-service dependency."""

from domain.outbound_service import OutboundService
from fastapi import Request


def get_outbound_service(request: Request) -> OutboundService:
    return request.app.state.services.outbound
