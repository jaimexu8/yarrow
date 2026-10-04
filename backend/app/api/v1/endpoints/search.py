from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Page, Region, RegionText, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.search import SearchHit

router = APIRouter()


def _like(term: str) -> str:
    # treat % and _ as normal characters
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/", response_model=list[SearchHit])
async def search_documents(
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    term = q.strip()
    if not term:
        return []

    matching_ids = (
        select(Document.id)
        .join(Page, Page.document_id == Document.id)
        .join(Region, Region.page_id == Page.id)
        .join(RegionText, RegionText.region_id == Region.id)
        .where(
            Document.owner_id == current_user.id,
            RegionText.text_content.ilike(_like(term), escape="\\"),
        )
        .distinct()
    )
    result = await db.execute(
        select(Document)
        .where(Document.id.in_(matching_ids))
        .order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())
