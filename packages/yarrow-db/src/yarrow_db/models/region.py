import uuid

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Region(Base):
    __tablename__ = "regions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id"), nullable=False)
    reading_order = Column(Integer)
    region_type = Column(String) # header, paragraph, figure, table, footer
    x0 = Column(Float)
    y0 = Column(Float)
    x1 = Column(Float)
    y1 = Column(Float)
    confidence = Column(Float)

    page = relationship("Page")

class RegionText(Base):
    __tablename__ = "region_texts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=False)
    text_content = Column(String)
    confidence = Column(Float)

    region = relationship("Region")

class RegionImage(Base):
    __tablename__ = "region_images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=False)
    image_key = Column(String)
    caption = Column(String)

    region = relationship("Region")

