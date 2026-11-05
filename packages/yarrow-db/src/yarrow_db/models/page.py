import uuid

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Page(Base):
    __tablename__ = "pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True
    )
    page_number = Column(Integer, nullable=False)
    width = Column(Float)
    height = Column(Float)
    status = Column(String)
    error_message = Column(String)
    error_code = Column(String(32))
    page_image_key = Column(String) # Storage key of the upright page image
    thumbnail_key = Column(String)
    model_name = Column(String(128))
    model_version = Column(String(64))

    document = relationship("Document")

    @property
    def has_thumbnail(self) -> bool:
        return self.thumbnail_key is not None
