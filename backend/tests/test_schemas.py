"""Response schemas must accept every value already stored in the database.

No database or HTTP client needed: these check the pydantic models directly.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import UserCreate, UserOut


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
