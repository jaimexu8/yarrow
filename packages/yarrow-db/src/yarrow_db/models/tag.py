import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Tag(Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # unique per owner, case-insensitive (index in migration 0015).
    name = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User")


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


Index(
    "uq_tags_owner_lower_name",
    Tag.owner_id,
    func.lower(Tag.name),
    unique=True,
)
