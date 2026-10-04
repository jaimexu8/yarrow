import logging

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Public, so it reports only up or down. The underlying errors can name
    hosts, users or credentials, so they go to the server log instead (NFR-6).
    """
    status = {"status": "healthy"}

    # Check DB
    try:
        await db.execute(text("SELECT 1"))
        status["database"] = "connected"
    except Exception:
        logger.exception("Health check: database unreachable")
        status["database"] = "unavailable"
        status["status"] = "unhealthy"

    # Check Valkey/Redis
    try:
        r = redis.from_url(f"redis://{settings.VALKEY_HOST}:{settings.VALKEY_PORT}/0")
        await r.ping()
        status["valkey"] = "connected"
    except Exception:
        logger.exception("Health check: Valkey unreachable")
        status["valkey"] = "unavailable"
        status["status"] = "unhealthy"

    return status
