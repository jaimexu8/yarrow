"""Outbound email.

One low-level ``send_email`` plus a helper per message type. Tests replace
``send_email`` to capture messages; with no SMTP_HOST configured the message is
logged instead, so the backend still runs (and the code can still be read from
the log) on a laptop without Mailpit.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    if not settings.SMTP_HOST:
        # WARNING, not INFO: uvicorn's default logging hides INFO for app
        # loggers, and this line is the only way to read the code in a local
        # run without a mail server. Never reached when SMTP_HOST is set.
        logger.warning(f"SMTP_HOST not set; email to {to} not sent:\n{body}")
        return

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        if settings.SMTP_USE_TLS:
            # Explicit context: starttls() with no context does not verify the
            # server certificate, so TLS alone would not stop impersonation.
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
        smtp.send_message(message)


def send_verification_code(to: str, code: str) -> None:
    send_email(
        to,
        "Your Yarrow verification code",
        (
            f"Your verification code is {code}.\n\n"
            f"It expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes. "
            "If you did not create a Yarrow account, you can ignore this email.\n"
        ),
    )
