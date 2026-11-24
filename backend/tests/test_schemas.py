"""Response schemas must accept every value already stored in the database.

No database or HTTP client needed: these check the pydantic models directly.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import TagCreate, UserCreate, UserOut
from app.schemas.document import DocumentOut, JobOut
from app.services.failure_messages import DOCUMENT_FAILED_MESSAGE

ERROR_STAGES = ["downloading", "parsing", "finalizing", "queue_timeout"]


def _user_out(email: str) -> UserOut:
    return UserOut.model_validate(
        {
            "id": uuid4(),
            "email": email,
            "name": None,
            "is_admin": False,
            "storage_used_bytes": 0,
        }
    )


def test_user_out_accepts_seeded_local_domain():
    """The seed accounts are admin@yarrow.local and user@yarrow.local.
    Rejecting them here made GET /auth/me return a 500."""
    assert _user_out("user@yarrow.local").email == "user@yarrow.local"


def test_user_out_accepts_ordinary_email():
    assert _user_out("someone@example.com").email == "someone@example.com"


def test_registration_still_rejects_reserved_domains():
    """Only output is relaxed: new sign-ups are still strictly validated."""
    with pytest.raises(ValidationError):
        UserCreate(email="new@yarrow.local", password="long-enough-password")


def test_tag_name_is_trimmed():
    assert TagCreate(name="  Invoice  ").name == "Invoice"


def test_empty_tag_name_is_rejected():
    with pytest.raises(ValidationError):
        TagCreate(name="   ")


def test_long_tag_name_is_rejected():
    with pytest.raises(ValidationError):
        TagCreate(name="x" * 41)


def _job_out(**extra) -> JobOut:
    return JobOut.model_validate({"id": uuid4(), **extra})


def _document_out(**extra) -> DocumentOut:
    return DocumentOut.model_validate(
        {
            "id": uuid4(),
            "filename": "notes.pdf",
            "file_size_bytes": 10,
            "file_type": "application/pdf",
            **extra,
        }
    )


@pytest.mark.parametrize("stage", ERROR_STAGES)
def test_job_out_accepts_known_error_stage(stage):
    assert _job_out(error_stage=stage).error_stage == stage


def test_job_out_accepts_null_error_stage():
    assert _job_out(error_stage=None).error_stage is None


def test_job_out_rejects_unknown_error_stage():
    with pytest.raises(ValidationError):
        _job_out(error_stage="exploding")


@pytest.mark.parametrize("stage", ERROR_STAGES)
def test_document_out_accepts_known_error_stage(stage):
    assert _document_out(error_stage=stage).error_stage == stage


def test_document_out_accepts_null_error_stage():
    assert _document_out(error_stage=None).error_stage is None


def test_document_out_rejects_unknown_error_stage():
    with pytest.raises(ValidationError):
        _document_out(error_stage="exploding")


def test_error_stage_survives_error_message_sanitizing():
    job = _job_out(
        status="failed", error_message="KeyError: 'x'", error_stage="parsing"
    )
    assert job.error_message == DOCUMENT_FAILED_MESSAGE
    assert job.error_stage == "parsing"

    document = _document_out(
        status="failed", error_message="KeyError: 'x'", error_stage="finalizing"
    )
    assert document.error_message == DOCUMENT_FAILED_MESSAGE
    assert document.error_stage == "finalizing"
