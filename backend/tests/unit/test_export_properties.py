"""Property-based tests for the pure filename / Content-Disposition helpers.

These pin the guarantees the download endpoints rely on: the stem helpers emit
only header-safe characters and bounded lengths, and ``content_disposition``
produces a well-formed RFC 6266 header (no CR/LF/NUL when fed a sanitized
name, the ``disposition; filename="..."`` shape, and the disposition token
preserved).
"""

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.export import (
    MAX_STEM_LENGTH,
    content_disposition,
    display_stem,
    safe_stem,
)

FALLBACK = "document-x"

# Filenames may contain anything a user or a malicious client could supply:
# control characters (CR, LF, NUL), quotes, semicolons, backslashes, path
# separators, unicode, dots, and very long runs.
filenames_strategy = st.text(
    st.sampled_from(
        list(
            "abzABC 0129"
            + "\x00"
            + "\x01"
            + "\r"
            + "\n"
            + "\t"
            + "\"'"
            + ";/"
            + "\\\\"
            + "."
            + "\u00e9"
            + "\u4e2d"
        )
    ),
    min_size=0,
    max_size=120,
)

# A filename whose extension-free stem is guaranteed non-empty after
# sanitization, so the helpers do not fall
# back. That way we can assert the shape of the sanitized output itself.
safe_filenames_strategy = st.text(
    st.sampled_from(list('abcxyz0189.-_ \u00e9\r\n\x00"\\;/')),
    min_size=1,
    max_size=120,
).filter(lambda s: any(ch.isalnum() for ch in s))

PROFILE = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# --- safe_stem ----------------------------------------------------------------


@given(filename=safe_filenames_strategy)
@PROFILE
def test_safe_stem_shape(filename):
    out = safe_stem(filename, FALLBACK)
    assert out != ""
    assert len(out) <= MAX_STEM_LENGTH
    assert re.fullmatch(r"[A-Za-z0-9._-]*", out)


# --- display_stem -------------------------------------------------------------


@given(filename=safe_filenames_strategy)
@PROFILE
def test_display_stem_shape(filename):
    out = display_stem(filename, FALLBACK)
    assert out != ""
    assert len(out) <= MAX_STEM_LENGTH
    # None of the header-breaking characters survive.
    assert not re.search(r'[\x00-\x1f\x7f"\\;/]', out)


# --- content_disposition ------------------------------------------------------


@given(
    disposition=st.sampled_from(["attachment", "inline"]),
    filename=safe_filenames_strategy,
)
@PROFILE
def test_content_disposition_shape(disposition, filename):
    # Feed the sanitized ASCII stem so the header cannot carry CR/LF/NUL.
    name = safe_stem(filename, FALLBACK)
    header = content_disposition(name, disposition=disposition)
    # No raw CR, LF, or NUL bytes.
    assert "\r" not in header
    assert "\n" not in header
    assert "\x00" not in header
    assert header.startswith(disposition + "; ")
    assert f'filename="{name}"' in header


@given(
    disposition=st.sampled_from(["attachment", "inline", "custom"]),
    name=st.text(st.sampled_from(list("abc0189.-_")), min_size=1, max_size=40),
)
@PROFILE
def test_content_disposition_preserves_token(disposition, name):
    header = content_disposition(name, disposition=disposition)
    assert header.split(";")[0].strip() == disposition
    assert f'filename="{name}"' in header
