import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class DocumentShare(Base):
    __tablename__ = "document_shares"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    shared_with_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    permission = Column(String)  # view, review
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document")
    shared_with_user = relationship("User")
