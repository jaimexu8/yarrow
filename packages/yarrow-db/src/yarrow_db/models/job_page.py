import uuid
from typing import Literal, get_args

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base

PageStatus = Literal["queued", "processing", "completed", "failed", "canceled"]
PAGE_STATUSES: tuple[PageStatus, ...] = get_args(PageStatus)
OPEN_PAGE_STATUSES: tuple[PageStatus, ...] = ("queued", "processing")


class JobPage(Base):
    __tablename__ = "job_pages"
    __table_args__ = (
        UniqueConstraint("job_id", "page_number", name="uq_job_pages_job_page"),
        CheckConstraint(
            "status IN ('queued','processing','completed','failed','canceled')",
            name="ck_job_pages_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    error_code = Column(String(32))
    error_message = Column(String)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    job = relationship("Job", back_populates="pages")
