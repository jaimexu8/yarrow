import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    status = {"status": "healthy"}

    # Check DB
    try:
        await db.execute(text("SELECT 1"))
        status["database"] = "connected"
    except Exception as e:
        status["database"] = f"error: {e!s}"
        status["status"] = "unhealthy"

    # Check Valkey/Redis
    try:
        r = redis.from_url(f"redis://{settings.VALKEY_HOST}:{settings.VALKEY_PORT}/0")
        await r.ping()
        status["valkey"] = "connected"
    except Exception as e:
        status["valkey"] = f"error: {e!s}"
        status["status"] = "unhealthy"

    return status
