from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, DocumentShare, User

from app.core.database import get_db
from app.core.security import get_current_user

_NO_ACCESS = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
)


@dataclass(frozen=True)
class DocumentAccess:
    """A document the caller may read"""

    document: Document
    permission: str = "owner"  # "owner", "review", "view"

    @property
    def can_edit(self) -> bool:
        # Determines whether a user can edit the document
        return self.permission in ("owner", "review")


async def get_document_access(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentAccess:
    """Resolve the document, checking ownership and shared ACLs."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalars().first()
    if document is None:
        raise _NO_ACCESS

    if document.owner_id == current_user.id:
        return DocumentAccess(document=document, permission="owner")

    # Check shared access control lists (ACLs)
    share_result = await db.execute(
        select(DocumentShare).where(
            DocumentShare.document_id == document_id,
            DocumentShare.shared_with_user_id == current_user.id,
        )
    )
    share = share_result.scalars().first()
    if share is not None:
        return DocumentAccess(document=document, permission=share.permission or "view")

    raise _NO_ACCESS


async def require_read_access(
    access: DocumentAccess = Depends(get_document_access),
) -> DocumentAccess:
    return access


async def require_edit_access(
    access: DocumentAccess = Depends(get_document_access),
) -> DocumentAccess:
    if not access.can_edit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "You have read-only access to this document",
                "code": "DOCUMENT_READ_ONLY",
            },
        )
    return access


async def require_owner_access(
    access: DocumentAccess = Depends(get_document_access),
    current_user: User = Depends(get_current_user),
) -> DocumentAccess:
    if access.document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Only the owner can delete this document",
                "code": "NOT_DOCUMENT_OWNER",
            },
        )
    return access


def require_completed(access: DocumentAccess) -> None:
    """Guard mutations and exports on a finished document"""
    if access.document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Document has not finished processing",
                "code": "DOCUMENT_NOT_PROCESSED",
            },
        )
