from datetime import UTC, datetime, timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from yarrow_db.models import User

from app.core.config import settings
from app.core.database import get_db
from app.core.mailer import send_verification_code
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_current_user,
    get_password_hash,
    oauth2_scheme,
    verify_password,
)
from app.core.token_denylist import revoke_token
from app.core.verification import (
    attempts_exhausted,
    can_send_code,
    clear_verification_state,
    code_matches,
    issue_verification_code,
    lock_user_by_email,
)
from app.schemas import (
    MessageResponse,
    ResendVerificationRequest,
    Token,
    UserCreate,
    UserOut,
    VerifyEmailRequest,
    normalize_email,
)

INVALID_CODE = "Invalid or expired verification code"
# Same reply whether or not the address is registered, so resend cannot be
# used to enumerate accounts.
RESEND_REPLY = "If that address has an unverified account, a new code was sent"

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalars().first() is not None:
        # The design document rate-limits account creation by requiring a
        # unique verified email (section 3.1), so a duplicate is a conflict
        # rather than a second account.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    user = User(
        email=payload.email,
        name=payload.name,
        hashed_password=get_password_hash(payload.password),
        is_active=True,
        # Unusable until the emailed code is confirmed (US-1). The design
        # document (3.1) relies on this to tie each account to a real mailbox.
        is_verified=False,
        storage_used_bytes=0,
    )
    code = issue_verification_code(user)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # After the response: a slow mail server must not slow down registration,
    # and a mail failure must not roll back an account that was created.
    background.add_task(send_verification_code, user.email, code)
    return user


@router.post("/verify", response_model=MessageResponse)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    # Row-locked: concurrent verify/resend calls for this account queue here.
    user = await lock_user_by_email(db, payload.email)

    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE
    )
    # One message for unknown email, already verified, wrong, expired and
    # exhausted codes: the client only needs to know this code will not work.
    if user is None or user.is_verified or attempts_exhausted(user):
        raise invalid
    if not code_matches(user, payload.code):
        user.verification_attempts = (user.verification_attempts or 0) + 1
        await db.commit()
        raise invalid

    user.is_verified = True
    clear_verification_state(user)
    await db.commit()
    return MessageResponse(message="Email verified")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    payload: ResendVerificationRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Row-locked, so parallel resends cannot all pass the throttle check.
    user = await lock_user_by_email(db, payload.email)

    # A throttled request gets the same 202 as a successful one, so resend
    # reveals neither whether the address exists nor whether it was sent.
    if user is not None and not user.is_verified and can_send_code(user):
        code = issue_verification_code(user)
        await db.commit()
        background.add_task(send_verification_code, user.email, code)
    return MessageResponse(message=RESEND_REPLY)


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    # OAuth2PasswordRequestForm calls the field `username`; Yarrow logs in by
    # email, so that is what is read out of it, in the same canonical form
    # registration stored it in.
    email = normalize_email(form_data.username)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    # Same response whether the email is unknown or the password is wrong, so
    # that login cannot be used to enumerate registered addresses.
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    # Checked after the password so that an unverified address cannot be
    # detected without knowing its password.
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Email address not verified"
        )

    token = create_access_token(
        {"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: str = Depends(oauth2_scheme),
    _current_user: User = Depends(get_current_user),
):
    """End this session (US-19).

    get_current_user has already refused a missing, invalid, expired or
    already-revoked token. Only this token is revoked; the user's sessions on
    other devices keep working.
    """
    payload = decode_access_token(token)
    if payload is None:
        # The token expired in the moment since get_current_user checked it.
        # An expired token is already unusable, so the session has ended.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
    try:
        await revoke_token(payload["jti"], expires_at)
    except RedisError:
        # Not a 204: telling the user they are logged out when the token still
        # works would be the one outcome this story exists to prevent. The
        # detail stays generic so no infrastructure names reach the client.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not log out right now. Please try again.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
