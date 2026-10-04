"""Download headers for the file-serving and export endpoints."""

import re
from urllib.parse import quote

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_CONTROL = re.compile(r'[\x00-\x1f\x7f"\\;/]+')

MAX_STEM_LENGTH = 80


def _strip_path_and_extension(filename: str | None) -> str:
    stem = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] if "." in stem else stem


def safe_stem(filename: str | None, fallback: str) -> str:
    """The filename without its extension, reduced to header-safe characters."""
    stem = _UNSAFE.sub("-", _strip_path_and_extension(filename)).strip("-._")
    return stem[:MAX_STEM_LENGTH] or fallback


def display_stem(filename: str | None, fallback: str) -> str:
    """Like safe_stem but keeping non-ASCII characters.

    For the filename form, which is percent-encoded and so can carry them.
    Quotes, semicolons, control characters and path separators are still removed,
    since those are what would break out of the header.
    """
    stem = _CONTROL.sub("", _strip_path_and_extension(filename)).strip()
    return stem[:MAX_STEM_LENGTH] or fallback


def content_disposition(filename: str, disposition: str = "attachment", unicode_filename: str | None = None) -> str:
    """An RFC 6266 header carrying both the ASCII and the UTF-8 form"""
    header = f'{disposition}; filename="{filename}"'
    if unicode_filename and unicode_filename != filename:
        header += f"; filename*=UTF-8''{quote(unicode_filename, safe='')}"
    return header
