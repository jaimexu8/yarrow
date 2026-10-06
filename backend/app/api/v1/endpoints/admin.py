from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, User

from app.core.database import get_db
from app.core.security import get_current_active_admin
from app.schemas.admin import AdminAccount

router = APIRouter()


@router.get("/accounts", response_model=list[AdminAccount])
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    # every account, with how many documents they own
    stmt = (
        select(
            User.id,
            User.name,
            User.email,
            User.created_at,
            User.is_admin,
            func.count(Document.id).label("document_count"),
        )
        .outerjoin(Document, Document.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        AdminAccount(
            id=row.id,
            name=row.name,
            email=row.email,
            created_at=row.created_at,
            is_admin=bool(row.is_admin),
            document_count=row.document_count,
        )
        for row in rows
    ]
