import logging
import os
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
from uuid import UUID, uuid4

import filetype
import pytest
from sqlalchemy import delete, select, text
from yarrow_db.models import (
    Document,
    DocumentShare,
    Job,
    Page,
    Region,
    RegionImage,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    User,
    Warning,
)
from yarrow_db.session import session_scope
from yarrow_storage import get_storage

logger = logging.getLogger(__name__)

TEST_DOCS_DIR = os.path.join(os.path.dirname(__file__), "test_docs")
DEFAULT_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class SeededDocument:
    document_id: UUID
    job_id: UUID
    storage_key: str
    filename: str
    file_size_bytes: int
    file_type: str


class TestInitializer:
    """Initializer for integration tests"""

    __test__ = False

    def __init__(self):
        self.records: List[SeededDocument] = []
        self.owner_id: Optional[UUID] = None
        self._storage = get_storage()

    @property
    def document_ids(self) -> List[UUID]:
        return [record.document_id for record in self.records]

    @property
    def job_ids(self) -> List[UUID]:
        return [record.job_id for record in self.records]

    def configure(self, filenames: List[str], filepaths: List[str]) -> List[SeededDocument]:
        """Upload each file and insert its document and job rows"""

        if len(filenames) != len(filepaths):
            raise ValueError(f"filenames and filepaths must be the same length, " f"got {len(filenames)} and {len(filepaths)}")

        owner: Optional[User] = None
        if self.owner_id is None:
            # Creates the user who holds the test document records
            owner = User(
                id=uuid4(),
                email=f"integration-{uuid4()}@yarrow.test",
                name="Integration Test",
                hashed_password="not-a-real-hash",
                is_active=True,
                is_verified=True,
                storage_used_bytes=0,
            )
            self.owner_id = owner.id

        new_records: List[SeededDocument] = []
        rows: List[object] = []

        for filename, filepath in zip(filenames, filepaths):
            with open(filepath, "rb") as handle:
                file_data = handle.read()

            document_id = uuid4()
            storage_key = f"documents/{document_id}/original"

            guessed = filetype.guess(file_data)
            file_type = guessed.mime if guessed is not None else DEFAULT_CONTENT_TYPE

            # Uploads the file to the storage
            self._storage.upload_file(BytesIO(file_data), storage_key)

            # Creates document and job objects in the database
            job_id = uuid4()
            rows.append(
                Document(
                    id=document_id,
                    owner_id=self.owner_id,
                    filename=filename,
                    file_size_bytes=len(file_data),
                    file_type=file_type,
                    storage_key=storage_key,
                    status="queued",
                )
            )
            rows.append(
                Job(
                    id=job_id,
                    document_id=document_id,
                    status="queued",
                    current_stage="queued",
                    pages_processed=0,
                    total_pages=0,
                )
            )
            new_records.append(
                SeededDocument(
                    document_id=document_id,
                    job_id=job_id,
                    storage_key=storage_key,
                    filename=filename,
                    file_size_bytes=len(file_data),
                    file_type=file_type,
                )
            )

        # Applies the changes to the database
        with session_scope() as session:
            if owner is not None:
                session.add(owner)
            session.add_all(rows)

        self.records.extend(new_records)
        return new_records

    def teardown(self):
        document_ids = self.document_ids
        owner_id = self.owner_id

        # Deletes the files uploaded by the test initializer
        for record in self.records:
            try:
                self._storage.delete_file(record.storage_key)
            except Exception:
                logger.exception(f"Failed to delete {record.storage_key}")

        # Deletes the database rows associated with the test documents
        if document_ids:
            self._delete_rows(document_ids)

        # Deletes the owner user if it was created
        if owner_id is not None:
            with session_scope() as session:
                session.execute(delete(User).where(User.id == owner_id))

        self.records.clear()
        self.owner_id = None

    @staticmethod
    def _delete_rows(document_ids: List[UUID]) -> None:
        """Delete the documents and everything related to them from the database."""

        page_ids = select(Page.id).where(Page.document_id.in_(document_ids))
        region_ids = select(Region.id).where(Region.page_id.in_(page_ids))
        table_ids = select(Table.id).where(Table.document_id.in_(document_ids))
        region_table_ids = select(RegionTable.id).where(RegionTable.region_id.in_(region_ids) | RegionTable.table_id.in_(table_ids))

        statements = [
            delete(TableCell).where(TableCell.region_table_id.in_(region_table_ids)),
            delete(RegionTable).where(RegionTable.id.in_(region_table_ids)),
            delete(RegionText).where(RegionText.region_id.in_(region_ids)),
            delete(RegionImage).where(RegionImage.region_id.in_(region_ids)),
            delete(Warning).where(Warning.page_id.in_(page_ids)),
            delete(Region).where(Region.id.in_(region_ids)),
            delete(Page).where(Page.id.in_(page_ids)),
            delete(Table).where(Table.id.in_(table_ids)),
            delete(Job).where(Job.document_id.in_(document_ids)),
            delete(DocumentShare).where(DocumentShare.document_id.in_(document_ids)),
            delete(Document).where(Document.id.in_(document_ids)),
        ]

        # Applies the deletions
        with session_scope() as session:
            for statement in statements:
                session.execute(statement.execution_options(synchronize_session=False))


@pytest.fixture
def test_initializer():
    initializer = TestInitializer()
    yield initializer
    initializer.teardown()
