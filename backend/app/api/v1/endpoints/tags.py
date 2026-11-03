from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.document import DocumentOut
from app.schemas.tag import TagCreate, TagListItem
from app.services.reprocess import documents_out
from app.services.tags import (
    add_tag_to_document,
    get_or_create_tag,
    list_tags_for_owner,
    remove_tag_from_document,
)

router = APIRouter()
document_router = APIRouter()

_NO_DOCUMENT = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
)


async def _owned_document(
    document_id: UUID, db: AsyncSession, current_user: User
) -> Document:
    document = await db.get(Document, document_id)
    if document is None or document.owner_id != current_user.id:
        raise _NO_DOCUMENT
    return document


@router.get("/", response_model=list[TagListItem])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """the signed-in user's tags, with how many documents each is on."""
    return await list_tags_for_owner(db, current_user.id)


@document_router.post("/{document_id}/tags", response_model=DocumentOut)
async def add_document_tag(
    document_id: UUID,
    body: TagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """add a tag to a document the caller owns. creates the tag if needed."""
    document = await _owned_document(document_id, db, current_user)
    tag = await get_or_create_tag(db, current_user.id, body.name)
    await add_tag_to_document(db, document.id, tag.id)
    await db.commit()
    await db.refresh(document)
    return (await documents_out(db, [document]))[0]


@document_router.delete(
    "/{document_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_document_tag(
    document_id: UUID,
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """remove a tag from a document. unused tags are deleted."""
    await _owned_document(document_id, db, current_user)
    removed = await remove_tag_from_document(db, document_id, tag_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
