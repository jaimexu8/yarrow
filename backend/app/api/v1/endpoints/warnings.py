from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Page, User, Warning

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.warning import WarningEntry

router = APIRouter()


@router.get("/", response_model=list[WarningEntry])
async def list_warnings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(
            Document.id,
            Document.filename,
            Page.page_number,
            Warning.warning_type,
            Warning.message,
        )
        .join(Page, Page.document_id == Document.id)
        .join(Warning, Warning.page_id == Page.id)
        .where(
            Document.owner_id == current_user.id,
            Document.deleted_at.is_(None),
        )
        .order_by(
            Document.created_at.desc(),
            Document.id,
            Page.page_number,
            Warning.created_at,
            Warning.id,
        ) # Newest document first
    )
    return [
        WarningEntry(
            document_id=document_id,
            filename=filename,
            page_number=page_number,
            warning_type=warning_type,
            message=message,
        )
        for document_id, filename, page_number, warning_type, message in result.all()
    ]
