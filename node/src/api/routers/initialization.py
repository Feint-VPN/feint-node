"""Initialization endpoints."""

from adapters.init_service import InitService
from api.depends import get_init_service, verify_api_secret
from api.schemas.initialization import InitRequest, InitResult, NodeSecrets
from fastapi import APIRouter, Depends, status
from utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["initialization"], dependencies=[Depends(verify_api_secret)])


@router.post("/initialize", response_model=InitResult, status_code=status.HTTP_200_OK)
async def initialize_node(
    body: InitRequest,
    svc: InitService = Depends(get_init_service),
) -> InitResult:
    result = await svc.initialize(body.domain, body.email, body.server_ip)
    return InitResult(**result)


@router.post("/secrets", response_model=NodeSecrets, status_code=status.HTTP_200_OK)
async def generate_secrets(
    svc: InitService = Depends(get_init_service),
) -> NodeSecrets:
    return await svc.generate_secrets()
