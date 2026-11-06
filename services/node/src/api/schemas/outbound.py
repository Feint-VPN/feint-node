"""API schemas for outbound access mutations."""

from pydantic import BaseModel, Field


class OutboundUsersRequest(BaseModel):
    users: set[str] = Field(min_length=1, max_length=500)
