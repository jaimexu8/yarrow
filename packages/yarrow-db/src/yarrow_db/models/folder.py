import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Folder(Base):
    __tablename__ = "folders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User")
    documents = relationship("Document", back_populates="folder")

    __table_args__ = (
        # unique per owner, ignoring case so "Invoices" and "invoices" clash
        Index(
            "uq_folders_owner_lower_name",
            "owner_id",
            func.lower(name),
            unique=True,
        ),
    )
