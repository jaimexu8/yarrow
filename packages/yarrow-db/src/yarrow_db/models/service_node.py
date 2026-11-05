import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class ServiceNode(Base):
    __tablename__ = "service_nodes"
    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_service_nodes_kind_name"),
        CheckConstraint("kind IN ('worker','inference')", name="ck_service_nodes_kind"),
        CheckConstraint(
            "status IN ('healthy','degraded','down','unknown')",
            name="ck_service_nodes_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(16), nullable=False)
    name = Column(String(255), nullable=False)
    hostname = Column(String(255), nullable=False)
    url = Column(String(2048))
    status = Column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    latency_ms = Column(Integer)
    last_checked_at = Column(DateTime)
    last_seen_at = Column(DateTime)
    last_error = Column(String(500))
    details = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
