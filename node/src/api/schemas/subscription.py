"""API schemas for subscription settings."""

from pydantic import BaseModel, Field


class SubscriptionSettingsResponse(BaseModel):
    subscription_enabled: bool
    server_domain: str
    sub_uri_template: str = Field(
        ...,
        description="Label template for generated subscription URIs",
    )


class SubscriptionSettingsUpdateRequest(BaseModel):
    sub_uri_template: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Allowed placeholders: {protocol}, {Protocol}, {username}",
    )
