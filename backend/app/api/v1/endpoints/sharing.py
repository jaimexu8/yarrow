import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import DocumentShare, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.deps import DocumentAccess, get_document_access, require_read_access
from app.schemas.sharing import (
    DocumentShareOut,
    ShareDocumentRequest,
    UpdateShareRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/{document_id}/share", response_model=DocumentShareOut)
@router.post("/{document_id}/shares", response_model=DocumentShareOut)
async def share_document(
    document_id: UUID,
    payload: ShareDocumentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    access: DocumentAccess = Depends(get_document_access),
):
    """Share a processed document with another registered user via email."""
    # Only document owner can share
    if access.document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the document owner can share this document",
        )

    # US-18: share a processed document
    if access.document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Only processed documents can be shared",
                "code": "DOCUMENT_NOT_PROCESSED",
            },
        )

    # Find the target user by email (case-insensitive)
    target_email = payload.email.lower().strip()
    result = await db.execute(
        select(User).where(func.lower(User.email) == target_email)
    )
    target_user = result.scalars().first()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No registered user found with email {payload.email}",
        )

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot share document with yourself",
        )

    # Check if already shared
    share_result = await db.execute(
        select(DocumentShare).where(
            DocumentShare.document_id == document_id,
            DocumentShare.shared_with_user_id == target_user.id,
        )
    )
    share = share_result.scalars().first()

    now = _utcnow()
    if share is not None:
        share.permission = payload.permission
        share.updated_at = now
    else:
        share = DocumentShare(
            id=uuid4(),
            document_id=document_id,
            shared_with_user_id=target_user.id,
            permission=payload.permission,
            created_at=now,
            updated_at=now,
        )
        db.add(share)

    await db.commit()
    await db.refresh(share)

    return DocumentShareOut(
        id=share.id,
        document_id=share.document_id,
        shared_with_user_id=target_user.id,
        shared_with_email=target_user.email,
        shared_with_name=target_user.name,
        permission=share.permission,
        created_at=share.created_at,
    )


@router.get("/{document_id}/shares", response_model=list[DocumentShareOut])
async def list_document_shares(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    access: DocumentAccess = Depends(require_read_access),
):
    """List all users who have shared access to this document."""
    result = await db.execute(
        select(DocumentShare, User)
        .join(User, DocumentShare.shared_with_user_id == User.id)
        .where(DocumentShare.document_id == document_id)
        .order_by(DocumentShare.created_at.desc())
    )
    rows = result.all()

    shares_out: list[DocumentShareOut] = []
    for share, user in rows:
        shares_out.append(
            DocumentShareOut(
                id=share.id,
                document_id=share.document_id,
                shared_with_user_id=user.id,
                shared_with_email=user.email,
                shared_with_name=user.name,
                permission=share.permission,
                created_at=share.created_at,
            )
        )
    return shares_out


@router.delete(
    "/{document_id}/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_document_share(
    document_id: UUID,
    share_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    access: DocumentAccess = Depends(get_document_access),
):
    """Revoke sharing permissions for a specific share."""
    share_result = await db.execute(
        select(DocumentShare).where(
            DocumentShare.id == share_id,
            DocumentShare.document_id == document_id,
        )
    )
    share = share_result.scalars().first()
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found",
        )

    # Only document owner or the recipient can remove the share
    if (
        access.document.owner_id != current_user.id
        and share.shared_with_user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to revoke this share",
        )

    await db.execute(delete(DocumentShare).where(DocumentShare.id == share_id))
    await db.commit()


@router.patch("/{document_id}/shares/{share_id}", response_model=DocumentShareOut)
async def update_share_permission(
    document_id: UUID,
    share_id: UUID,
    payload: UpdateShareRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    access: DocumentAccess = Depends(get_document_access),
):
    """Update permission for an existing document share."""
    if access.document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the document owner can update sharing permissions",
        )

    share_result = await db.execute(
        select(DocumentShare, User)
        .join(User, DocumentShare.shared_with_user_id == User.id)
        .where(
            DocumentShare.id == share_id,
            DocumentShare.document_id == document_id,
        )
    )
    row = share_result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found",
        )

    share, user = row
    share.permission = payload.permission
    share.updated_at = _utcnow()
    await db.commit()
    await db.refresh(share)

    return DocumentShareOut(
        id=share.id,
        document_id=share.document_id,
        shared_with_user_id=user.id,
        shared_with_email=user.email,
        shared_with_name=user.name,
        permission=share.permission,
        created_at=share.created_at,
    )
