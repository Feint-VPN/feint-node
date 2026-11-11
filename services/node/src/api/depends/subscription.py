"""Resolve the application-owned subscription workflow."""

from fastapi import Request
from services.subscription import SubscriptionService


def get_subscription_service(request: Request) -> SubscriptionService:
    return request.app.state.services.subscription
