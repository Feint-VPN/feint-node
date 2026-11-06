"""Statistics endpoints — backed by the in-process TrafficTracker."""

from adapters.traffic_tracker import TrafficTracker
from api.depends import get_traffic_tracker, verify_api_secret
from api.schemas.user import UserStats
from fastapi import APIRouter, Depends
from utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["statistics"], dependencies=[Depends(verify_api_secret)])


@router.get("/user/{username}/stats", response_model=UserStats)
async def get_user_stats(
    username: str,
    tracker: TrafficTracker = Depends(get_traffic_tracker),
) -> UserStats:
    data = await tracker.get_user(username)
    upload = int(data.get("upload", 0))
    download = int(data.get("download", 0))
    return UserStats(
        username=username,
        upload_bytes=upload,
        download_bytes=download,
        total_bytes=upload + download,
        last_seen=data.get("last_seen"),
        available=bool(data.get("available", False)),
    )


@router.get("/stats", response_model=list[UserStats])
async def get_all_stats(
    tracker: TrafficTracker = Depends(get_traffic_tracker),
) -> list[UserStats]:
    snapshot = await tracker.get_all()
    available = await tracker.is_available()
    result: list[UserStats] = []
    for username, data in snapshot.items():
        upload = int(data.get("upload", 0))
        download = int(data.get("download", 0))
        result.append(
            UserStats(
                username=username,
                upload_bytes=upload,
                download_bytes=download,
                total_bytes=upload + download,
                last_seen=data.get("last_seen"),
                available=available,
            )
        )
    return result
