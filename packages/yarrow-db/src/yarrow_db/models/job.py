import uuid
from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base

JobStage = Literal[
    "queued",
    "downloading",
    "preparing",
    "parsing",
    "saving",
    "finished",
    "failed",
    "canceled",
]
JOB_STAGES: tuple[JobStage, ...] = get_args(JobStage)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_document_id_created_at", "document_id", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    status = Column(String)  # queued, processing, completed, failed, canceled
    current_stage = Column(String)
    pages_processed = Column(Integer, default=0)
    total_pages = Column(Integer, default=0)
    error_message = Column(String)
    # Celery task id returned by send_task, needed to revoke a queued job (US-42).
    celery_task_id = Column(String, index=True)
    canceled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    cancel_reason = Column(String(500))
    retry_of_job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    model_name = Column(String(128))
    model_version = Column(String(64))
    pipeline_version = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    document = relationship("Document")
    pages = relationship(
        "JobPage",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JobPage.page_number",
    )
