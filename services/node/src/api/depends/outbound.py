"""Outbound-service dependency."""

from fastapi import Request
from services.outbound import OutboundService


def get_outbound_service(request: Request) -> OutboundService:
    return request.app.state.services.outbound
