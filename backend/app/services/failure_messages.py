"""What a user is told when processing fails (US-11).

The worker stores its own reasons in ``error_message``, and some of them are
raw exception text that can name hosts, storage keys or library internals.
Only messages written for users are shown as-is; anything else is replaced
with a plain explanation, and the detail stays in the worker's log (NFR-6).
"""

import re

QUEUE_FAILED_MESSAGE = "The file was uploaded, but processing could not be started. " "Please try again later."
DOCUMENT_FAILED_MESSAGE = "Processing failed. Please try uploading the document again."
INTERRUPTED_MESSAGE = "Processing stopped before it finished."
PAGE_FAILED_MESSAGE = "This page could not be processed."

# The worker's summary of failed pages, e.g. "2 of 10 page(s) failed: 3, 7"
# or "12 of 40 page(s) failed: 1, 2, ... (+2 more)". Only numbers, so safe.
_FAILED_PAGES_SUMMARY = re.compile(r"^\d+ of \d+ page\(s\) failed: \d+(, \d+)*(, \.\.\. \(\+\d+ more\))?$")


def is_user_facing(message: str) -> bool:
    return message in (QUEUE_FAILED_MESSAGE, INTERRUPTED_MESSAGE) or bool(_FAILED_PAGES_SUMMARY.match(message))


def public_error_message(message: str | None, status: str | None, fallback: str) -> str | None:
    """The reason to show for a document, job or page with this status.

    A failed item always gets a reason, so the user is never left with a
    bare "Failed". Anything not written for users becomes ``fallback``.
    """
    if message and is_user_facing(message):
        return message
    if status == "failed":
        return fallback
    # Not failed and nothing safe to say: show no reason at all.
    return None
