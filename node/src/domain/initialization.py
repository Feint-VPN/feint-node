"""Typed results produced while initializing a node."""

from pydantic import BaseModel, Field


class NodeSecrets(BaseModel):
    api_secret: str = Field(min_length=1)
    reality_private_key: str = Field(min_length=1)
    reality_public_key: str = Field(min_length=1)
    reality_short_id: str = Field(min_length=1)
    shadowsocks_password: str = Field(min_length=1)
