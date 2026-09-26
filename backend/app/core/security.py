from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from yarrow_db.models import User

from .config import settings

# Assuming we have a get_db dependency in backend/app/core/database.py
from .database import get_db
from .token_denylist import is_token_revoked

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

# bcrypt hashes at most 72 bytes of input and, since 5.0, raises rather than
# silently truncating. Passwords are capped at this in the request schema; the
# helpers below clip defensively so that a caller bypassing the schema gets a
# failed check rather than a 500.
BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    """UTF-8 bytes, clipped to what bcrypt will accept."""
    return password.encode("utf-8")[:BCRYPT_MAX_BYTES]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(plain_password), hashed_password.encode("utf-8"))
    except ValueError:
        # A stored hash that is not a valid bcrypt hash is a failed login, not
        # a server error.
        return False


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # jti ("JWT ID") is unique per token, so logging out can revoke exactly
    # this one session without touching the user's other devices (US-19).
    to_encode.update({"exp": expire, "jti": str(uuid4())})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    # A token without a jti predates US-19 and could never be revoked, so it
    # is refused: the user signs in once more and gets a revocable one.
    jti = payload.get("jti")
    if not jti:
        raise credentials_exception
    try:
        revoked = await is_token_revoked(jti)
    except RedisError:
        # Fail closed: if the denylist can't be read, a logged-out token
        # can't be told apart from a live one, so no request is let through.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is temporarily unavailable. Please try again.",
        ) from None
    if revoked:
        raise credentials_exception

    try:
        # The column is a real UUID and asyncpg will not coerce a string into
        # one, so a malformed sub has to be rejected here rather than becoming
        # a database error on the query below.
        user_uuid = UUID(user_id)
    except ValueError:
        raise credentials_exception from None

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception

    # "sv" is the user's session_version when this token was issued. A
    # password reset increments it, which ends every older session (US-67).
    if payload.get("sv") != user.session_version:
        raise credentials_exception
    return user


async def get_current_active_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user
