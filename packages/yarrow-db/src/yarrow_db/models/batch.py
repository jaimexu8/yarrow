import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Batch(Base):
    """Files uploaded together in one "Upload" click (US-31).

    The id is chosen by the browser and sent with every file in the run; the
    first upload request with an unseen id creates the row. A batch is only a
    label for grouping documents and reporting their progress: each document
    keeps its own job, so one failure never fails the others.

    US-60: ``sealed_at`` is when the browser finished sending the run's files
    (cleared when another file joins). ``completed_at`` is when every live
    document was last seen in a final state (cleared on join or when one goes
    back to the queue). ``claimed_at`` is a short lease taken by the tab that
    is about to show the completion notification; ``notified_at`` is set when
    that tab acknowledges it, and never cleared, so the owner is told about
    each batch once.
    """

    __tablename__ = "batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    sealed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    notified_at = Column(DateTime, nullable=True)

    owner = relationship("User")

    __table_args__ = (
        # The notifier asks for one user's unannounced batches on every check.
        Index(
            "ix_batches_owner_unnotified",
            "owner_id",
            postgresql_where=text("notified_at IS NULL"),
        ),
    )
