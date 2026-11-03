"""Load and change a user's document tags."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import DocumentTag, Tag

from app.schemas.tag import TagListItem, TagOut


async def tags_for_documents(
    db: AsyncSession, document_ids: Sequence[UUID]
) -> dict[UUID, list[TagOut]]:
    """tags on each document, sorted by name."""
    tagged: dict[UUID, list[TagOut]] = {document_id: [] for document_id in document_ids}
    if not document_ids:
        return tagged
    rows = await db.execute(
        select(DocumentTag.document_id, Tag.id, Tag.name)
        .join(Tag, Tag.id == DocumentTag.tag_id)
        .where(DocumentTag.document_id.in_(list(document_ids)))
        .order_by(func.lower(Tag.name), Tag.id)
    )
    for document_id, tag_id, name in rows.all():
        tagged.setdefault(document_id, []).append(TagOut(id=tag_id, name=name))
    return tagged


async def list_tags_for_owner(db: AsyncSession, owner_id: UUID) -> list[TagListItem]:
    """this user's tags, with how many documents each is on."""
    usage = func.count(DocumentTag.document_id)
    rows = await db.execute(
        select(Tag.id, Tag.name, usage)
        .outerjoin(DocumentTag, DocumentTag.tag_id == Tag.id)
        .where(Tag.owner_id == owner_id)
        .group_by(Tag.id)
        .order_by(func.lower(Tag.name), Tag.id)
    )
    return [
        TagListItem(id=tag_id, name=name, document_count=count)
        for tag_id, name, count in rows.all()
    ]


async def get_or_create_tag(db: AsyncSession, owner_id: UUID, name: str) -> Tag:
    """reuse this owner's tag with the same name, ignoring case."""
    existing = await db.scalar(
        select(Tag).where(
            Tag.owner_id == owner_id, func.lower(Tag.name) == name.lower()
        )
    )
    if existing is not None:
        return existing

    tag = Tag(id=uuid4(), owner_id=owner_id, name=name)
    try:
        # a savepoint so a unique-name clash does not abort the request.
        async with db.begin_nested():
            db.add(tag)
            await db.flush()
    except IntegrityError:
        # another request created the same name at the same moment.
        tag = await db.scalar(
            select(Tag).where(
                Tag.owner_id == owner_id, func.lower(Tag.name) == name.lower()
            )
        )
        if tag is None:
            raise
    return tag


async def add_tag_to_document(
    db: AsyncSession, document_id: UUID, tag_id: UUID
) -> None:
    """attach a tag; doing it twice is a no-op."""
    already = await db.scalar(
        select(DocumentTag).where(
            DocumentTag.document_id == document_id, DocumentTag.tag_id == tag_id
        )
    )
    if already is not None:
        return
    db.add(DocumentTag(document_id=document_id, tag_id=tag_id))
    await db.flush()


async def remove_tag_from_document(
    db: AsyncSession, document_id: UUID, tag_id: UUID
) -> bool:
    """drop the tag from this document. returns false if it was not on it.

    if the tag is then on no documents, the tag row is removed too.
    """
    result = await db.execute(
        delete(DocumentTag).where(
            DocumentTag.document_id == document_id, DocumentTag.tag_id == tag_id
        )
    )
    if result.rowcount == 0:
        return False
    remaining = await db.scalar(
        select(func.count())
        .select_from(DocumentTag)
        .where(DocumentTag.tag_id == tag_id)
    )
    if not remaining:
        await db.execute(delete(Tag).where(Tag.id == tag_id))
    return True
