import uuid

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Table(Base):
    __tablename__ = "tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    row_count = Column(Integer)
    col_count = Column(Integer)
    is_stitched = Column(Boolean, default=False)
    title = Column(String)

    document = relationship("Document")


class RegionTable(Base):
    __tablename__ = "region_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=False)
    table_id = Column(UUID(as_uuid=True), ForeignKey("tables.id"), nullable=False)
    reading_order = Column(Integer)
    row_start = Column(Integer)
    row_end = Column(Integer)
    col_start = Column(Integer)
    col_end = Column(Integer)

    region = relationship("Region")
    table = relationship("Table")


class TableCell(Base):
    __tablename__ = "table_cells"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_table_id = Column(
        UUID(as_uuid=True), ForeignKey("region_tables.id"), nullable=False
    )
    row_idx = Column(Integer)
    col_idx = Column(Integer)
    row_span = Column(Integer, default=1)
    col_span = Column(Integer, default=1)
    text_content = Column(String)
    x0 = Column(Float)
    y0 = Column(Float)
    x1 = Column(Float)
    y1 = Column(Float)
    is_header = Column(Boolean, default=False)

    region_table = relationship("RegionTable")
