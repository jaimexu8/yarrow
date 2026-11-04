import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class UploadSession(Base):
    """One resumable upload of a large file (US-80).

    The browser declares the file's size and chunk size up front, sends the
    chunks one request each, and finalizes once every chunk is confirmed.
    Chunks live as temporary objects in the object store (see UploadChunk),
    so any backend process can accept the next chunk or assemble the file.

    status uses the shared vocabulary: "processing" while chunks are being
    received, "completed" once the document has been created (document_id is
    then set), "failed" when the assembled file failed validation. An aborted
    session is deleted outright. Rows past expires_at are swept, chunks and
    all, by app.services.upload_sessions.
    """

    __tablename__ = "upload_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String, nullable=False)
    total_size_bytes = Column(BigInteger, nullable=False)
    chunk_size_bytes = Column(Integer, nullable=False)
    chunk_count = Column(Integer, nullable=False)
    # Optional whole-file checksum the client declared, checked at finalize.
    expected_sha256 = Column(String(64))
    # sha256 of the assembled file, set at finalize. A repeated finalize
    # from a different local file is refused by comparing against it.
    file_sha256 = Column(String(64))
    # The US-31 run this file belongs to. Not a foreign key: the batch row is
    # only created when the first file of the run is stored, which for a
    # resumable upload is at finalize.
    batch_id = Column(UUID(as_uuid=True))
    status = Column(String, nullable=False, default="processing")
    error_message = Column(String)
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)

    chunks = relationship(
        "UploadChunk",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="UploadChunk.chunk_index",
    )


class UploadChunk(Base):
    """One confirmed chunk of an upload session (US-80).

    A row is written only after its object is stored, so a row always means
    "the server has these bytes". sha256 is what makes a retried chunk safe:
    the same index with the same bytes is accepted again without storing
    anything, and different bytes for a confirmed index are refused.
    """

    __tablename__ = "upload_chunks"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "chunk_index", name="uq_upload_chunks_session_index"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    storage_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("UploadSession", back_populates="chunks")
