"""Which failure reasons are safe to show a user (US-11, NFR-6)."""

import pytest

from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    QUEUE_FAILED_MESSAGE,
    is_user_facing,
    public_error_message,
)


@pytest.mark.parametrize(
    "message",
    [
        "1 of 2 page(s) failed: 2",
        "3 of 3 page(s) failed: 1, 2, 3",
        "12 of 40 page(s) failed: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, ... (+2 more)",
        QUEUE_FAILED_MESSAGE,
    ],
)
def test_messages_written_for_users_are_kept(message):
    assert is_user_facing(message)
    assert public_error_message(message, "failed", "fallback") == message


@pytest.mark.parametrize(
    "message",
    [
        "TimeoutError: inference server did not respond",
        "Uploaded file not found in storage: documents/abc/original",
        "1 of 2 page(s) failed: 2 (host=inference:8080)",
        "",
    ],
)
def test_anything_else_is_replaced_when_failed(message):
    assert not is_user_facing(message)
    assert (
        public_error_message(message, "failed", DOCUMENT_FAILED_MESSAGE)
        == DOCUMENT_FAILED_MESSAGE
    )


@pytest.mark.parametrize("status", ["queued", "processing", "completed", None])
def test_unsafe_text_is_dropped_when_not_failed(status):
    assert public_error_message("KeyError: 'x'", status, "fallback") is None
