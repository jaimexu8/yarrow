"""Query parsing and matching for exact-phrase, OCR-tolerant search.

Pure functions with no database access, so the endpoint and the tests share
one definition of what a query means. The frontend relies on the returned
match spans (which cover the characters actually in the text, not the query),
so near matches from OCR confusions or typos can be flagged in the UI.

Grammar for the raw ``q`` string:
  * double-quoted segments are phrase terms. The words must appear in
    order with only whitespace between them;
  * everything else splits into single-word terms;
  * an opening quote with no closing one runs to the end of the query;
  * terms are combined with AND (every term must match the same text).

Matching is case-insensitive and tolerates the common OCR confusions
``1``/``l``/``I`` and ``0``/``O``. A word term matches when it is a
substring (with confusables folded) or, for words of three letters or more
that contain a letter, when a nearby token is within one edit (a typo or
transposition).
"""

import re
import unicodedata
from dataclasses import dataclass

# Whitespace runs that may separate the words of a phrase. Written as an
# explicit character class (not \s) so the fragment is valid in both
# PostgreSQL's POSIX regex and Python's re without extra escaping.
_SPACE = r"[ \t\r\n]"
_MIN_FUZZY_WORD = 3
_MAX_FUZZY_WORD = 40

_CONFUSABLES = {
    "1": "1lI",
    "l": "1lI",
    "i": "1lIi",
    "0": "0O",
    "o": "0Oo",
}


@dataclass(frozen=True)
class Term:
    """A single AND term: one word, or an ordered phrase (quoted=True)."""

    words: tuple[str, ...]
    quoted: bool = False

    def pattern(self) -> str:
        """Regex fragment that matches this term, in either regex dialect."""
        if self.quoted:
            return (_SPACE + "+").join(_token_pattern(w) for w in self.words)
        return _token_pattern(self.words[0])


def _token_pattern(word: str) -> str:
    out = []
    for ch in word:
        if ch in _CONFUSABLES:
            out.append("[" + _CONFUSABLES[ch] + "]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def parse_query(raw: str) -> list[Term]:
    """Split the raw query into AND terms. Blank query -> empty list."""
    text = unicodedata.normalize("NFKC", raw or "").strip()
    terms: list[Term] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if text[i] == '"':
            end = text.find('"', i + 1)
            if end == -1:
                segment = text[i + 1 :]
                i = n
            else:
                segment = text[i + 1 : end]
                i = end + 1
            words = tuple(w for w in segment.split() if w)
            if words:
                terms.append(Term(words, quoted=True))
            continue
        start = i
        while i < n and text[i] not in ' \t\r\n"':
            i += 1
        word = text[start:i]
        if word:
            terms.append(Term((word,), quoted=False))
    return terms


def terms_match(text: str, terms: list[Term]) -> bool:
    """True when every term matches the text (AND semantics)."""
    if not text:
        return False
    lowered = text.lower()
    return all(_term_matches(lowered, term) for term in terms)


def _term_matches(lowered: str, term: Term) -> bool:
    if term.quoted:
        return _regex_search(term.pattern(), lowered) is not None
    word = term.words[0]
    if _regex_search(_token_pattern(word), lowered) is not None:
        return True
    if not _fuzzy_eligible(word):
        return False
    return _fuzzy_span(word, lowered) is not None


def _fuzzy_eligible(word: str) -> bool:
    """Words with a letter may be fuzzy-matched. Purely numeric values
    (2024, 1.5) keep only the confusable substring match, so 2023 never
    matches 2024."""
    return _MIN_FUZZY_WORD <= len(word) <= _MAX_FUZZY_WORD and any(
        ch.isalpha() for ch in word
    )


def first_match_span(text: str, term: Term) -> tuple[int, int] | None:
    """(start, end) of the first occurrence of the term in text.

    The span covers the characters actually present in the text (which for an
    OCR confusable or typo differ from the query, e.g. "l.1" for "1.1") so a
    client can tell a near match from an exact one. None when absent.
    """
    if not text:
        return None
    lowered = text.lower()
    if term.quoted:
        match = _regex_search(term.pattern(), lowered)
    else:
        word = term.words[0]
        match = _regex_search(_token_pattern(word), lowered)
        if match is None and _fuzzy_eligible(word):
            match = _fuzzy_span(word, lowered)
    return None if match is None else match.span()


def _regex_search(fragment: str, text: str) -> re.Match | None:
    if not fragment:
        return None
    return re.search(fragment, text, flags=re.IGNORECASE)


def _fuzzy_span(word: str, lowered: str) -> re.Match | None:
    """First whitespace token within one edit of word, or None."""
    for match in re.finditer(r"\S+", lowered):
        if within_one_edit(word, match.group(0).lower()):
            return match
    return None


def within_one_edit(a: str, b: str) -> bool:
    """True when a and b are at most one edit apart.

    An edit is an insertion, deletion, substitution, or a single adjacent
    transposition (the optimal-string-alignment measure). The transposition is
    what lets an OCR "recieve" match "receive"; plain Levenshtein scores that
    swap as two edits.
    """
    la, lb = len(a), len(b)
    if la > lb:
        a, b, la, lb = b, a, lb, la
    if lb - la > 1:
        return False
    table = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        table[i][0] = i
    for j in range(1, lb + 1):
        table[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            table[i][j] = min(
                table[i - 1][j] + 1,  # delete a[i-1]
                table[i][j - 1] + 1,  # insert b[j-1]
                table[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else 1),  # substitute
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                table[i][j] = min(table[i][j], table[i - 2][j - 2] + 1)  # transposition
    return table[la][lb] <= 1
