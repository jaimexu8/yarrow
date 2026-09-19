import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Warning(Base):
    __tablename__ = "warnings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id"), nullable=False)
    warning_type = Column(String)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    page = relationship("Page")

