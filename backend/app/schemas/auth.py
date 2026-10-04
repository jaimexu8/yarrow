"""User, token, email verification and password reset models."""

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field


def normalize_email(value: str) -> str:
    """The one canonical form an email is stored and looked up in.

    EmailStr lowercases only the domain, so "Alice@Example.com" and
    "alice@example.com" would otherwise be two accounts, and a user who
    registered with one could not sign in with the other.
    """
    return value.strip().lower()


# Use for every email that arrives in a request body.
NormalizedEmail = Annotated[EmailStr, AfterValidator(normalize_email)]


def _within_bcrypt_limit(value: str) -> str:
    # 72 bytes, not characters: that is bcrypt's hard input limit, and it
    # raises on anything longer rather than truncating.
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


# The one set of password rules, for sign-up and for choosing a new password.
NewPassword = Annotated[str, Field(min_length=8), AfterValidator(_within_bcrypt_limit)]


class UserCreate(BaseModel):
    email: NormalizedEmail
    password: NewPassword
    name: str | None = None


class UserDelete(BaseModel):
    # Re-entered on deletion so that a stolen or left-open session alone cannot
    # destroy the account.
    password: str
    
class UserUpdate(BaseModel):
    name: str | None = None
    email: NormalizedEmail
    current_password: str
    password: NewPassword | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # Plain str on the way out: the value comes from our own database and was
    # validated when it came in. Re-validating it here rejected the seeded
    # "@yarrow.local" accounts (".local" is a reserved domain), which made
    # /auth/me crash with a 500 for them.
    email: str
    name: str | None = None
    is_admin: bool
    storage_used_bytes: int


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class VerifyEmailRequest(BaseModel):
    email: NormalizedEmail
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class ResendVerificationRequest(BaseModel):
    email: NormalizedEmail


class PasswordResetRequest(BaseModel):
    email: NormalizedEmail


class PasswordResetConfirm(BaseModel):
    # secrets.token_urlsafe(32) gives 43 characters; the bounds only reject
    # obvious junk before any hashing or database work.
    token: str = Field(min_length=20, max_length=200)
    new_password: NewPassword


class MessageResponse(BaseModel):
    message: str
