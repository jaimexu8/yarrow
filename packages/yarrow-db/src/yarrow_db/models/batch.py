import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Batch(Base):
    """Files uploaded together in one "Upload" click (US-31).

    The id is chosen by the browser and sent with every file in the run; the
    first upload request with an unseen id creates the row. A batch is only a
    label for grouping documents and reporting their progress: each document
    keeps its own job, so one failure never fails the others.
    """

    __tablename__ = "batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User")
