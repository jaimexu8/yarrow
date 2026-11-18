"""Property-based tests for the pure search-query parser and matcher.

These pin the behaviours the endpoint and the SQL layer rely on: the grammar
of ``parse_query``, the confusable folding (1/l/I and 0/O), the edit-distance
tolerance, and the shape of the match spans the frontend consumes.

The module is pure (no database), so these run without a connection. They are
kept fast: bounded example counts and short strings.
"""

import re
import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.search_query import (
    Term,
    first_match_span,
    parse_query,
    terms_match,
    within_one_edit,
)

# A short alphabet that mixes ordinary letters and digits with the confusable
# characters (1 l I, 0 O o) so the folding logic is actually exercised.
CONFUSABLE_ALPHABET = string.ascii_lowercase + string.digits + "lLIoO"

# A fixed, non-identity permutation of the confusable class keys. The module
# folds the lowercase/digit forms (1, l, i and 0, o) into confusable classes,
# but treats uppercase I/O as plain literals (case-folded only), so the
# permutation is restricted to the lowercase/digit keys to stay within the
# symmetric classes.
PERMUTE = {"1": "l", "l": "1", "i": "i", "0": "o", "o": "0"}
# Alphabet for the permutation test: confusable lowercase/digit keys plus the
# non-confusable letters/digits, but NOT uppercase I/O (which are literals).
PERMUTE_ALPHABET = string.ascii_lowercase + string.digits

# Two deterministic within-class perturbations (used to build a text variant
# and a query variant that are confusable-equivalent to the original word).
TEXT_PERTURB = {"1": "l", "l": "i", "i": "1", "0": "o", "o": "0"}
QUERY_PERTURB = {"1": "i", "l": "1", "i": "l", "0": "o", "o": "0"}

word_strategy = st.text(
    st.sampled_from(list(CONFUSABLE_ALPHABET)), min_size=1, max_size=10
)
text_strategy = st.text(
    st.sampled_from(list(CONFUSABLE_ALPHABET + " \t")), min_size=0, max_size=40
)

PROFILE = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def apply_mapping(word: str, mapping: dict[str, str]) -> str:
    """Substitute each character via ``mapping``; unmapped characters pass
    through unchanged."""
    return "".join(mapping.get(ch, ch) for ch in word)


# --- parse_query -----------------------------------------------------------


@given(raw=text_strategy)
@PROFILE
def test_parse_query_never_raises_and_patterns_compile(raw):
    terms = parse_query(raw)
    assert isinstance(terms, list)
    for term in terms:
        re.compile(term.pattern())
        assert len(term.words) >= 1
        for word in term.words:
            assert word != ""


@given(raw=text_strategy)
@PROFILE
def test_parse_query_terms_have_no_whitespace_or_quotes(raw):
    for term in parse_query(raw):
        for word in term.words:
            assert " " not in word
            assert "\t" not in word
            assert '"' not in word


# --- within_one_edit -------------------------------------------------------


def osa_distance(a: str, b: str) -> int:
    """Optimal-string-alignment distance
    A transposition of two adjacent characters costs one,
    which is what lets "recieve" match "receive".
    """
    la, lb = len(a), len(b)
    table = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        table[i][0] = i
    for j in range(lb + 1):
        table[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                table[i][j] = min(table[i][j], table[i - 2][j - 2] + 1)
    return table[la][lb]


@given(a=word_strategy, b=word_strategy)
@settings(max_examples=400, deadline=None)
def test_within_one_edit_agrees_with_independent_osa(a, b):
    assert within_one_edit(a, b) == (osa_distance(a, b) <= 1)


@given(a=word_strategy, b=word_strategy)
@PROFILE
def test_within_one_edit_is_symmetric(a, b):
    assert within_one_edit(a, b) == within_one_edit(b, a)


# --- terms_match -----------------------------------------------------------


@given(
    text=text_strategy,
    query=st.text(
        st.sampled_from(list(CONFUSABLE_ALPHABET + " ")), min_size=0, max_size=30
    ),
)
@PROFILE
def test_terms_match_is_monotone_under_term_addition(text, query):
    terms = parse_query(query)
    if terms_match(text, terms):
        for i in range(len(terms)):
            assert terms_match(text, terms[:i])


@given(word=word_strategy, text=text_strategy)
@PROFILE
def test_verbatim_containment_with_perturbation(word, text):
    # If the text contains a confusable/case variant of the query word, a
    # one-word query for that word matches. Both the embedded word and the
    # query are perturbed
    text_word = apply_mapping(word, TEXT_PERTURB)
    query_word = apply_mapping(word, QUERY_PERTURB)
    full_text = f"{text} {text_word} "
    assert terms_match(full_text, parse_query(query_word))


@given(word=word_strategy)
@PROFILE
def test_confusable_variant_of_query_matches(word):
    variant = apply_mapping(word, TEXT_PERTURB)
    assert terms_match(variant, parse_query(word))


@given(
    word=st.text(st.sampled_from(list(PERMUTE_ALPHABET)), min_size=1, max_size=10),
    text=st.text(
        st.sampled_from(list(PERMUTE_ALPHABET + " \t")), min_size=0, max_size=40
    ),
)
@PROFILE
def test_confusable_permutation_invariance(word, text):
    # Permuting the confusable class keys consistently in both the query and
    # the text preserves the match outcome. The permutation maps each class to
    # itself (1<->l, 0<->o, i->i), so a text character that matches a query
    # class still matches the permuted class. Uppercase I/O are excluded
    # because the module treats them as case-folded literals, not class keys.
    permuted_word = apply_mapping(word, PERMUTE)
    permuted_text = apply_mapping(text, PERMUTE)
    assert terms_match(permuted_text, parse_query(permuted_word)) == terms_match(
        text, parse_query(word)
    )


@given(
    digits=st.text("0123456789", min_size=1, max_size=6),
    pos=st.integers(min_value=0, max_value=5),
    replacement=st.sampled_from("0123456789"),
)
@PROFILE
def test_numeric_query_rejects_single_digit_change(digits, pos, replacement):
    # No-fuzzy-numbers: a purely numeric query matches only the exact digits
    # (within the 0/O and 1/l/I confusable folds). Changing one digit to a
    # digit that shares no confusable class with the replaced one must not
    # match.
    pos = min(pos, len(digits) - 1)
    if digits[pos] == replacement:
        return
    # No two distinct digits are confusable with each other (0's partners are
    # O/o, 1's are l/i; 2-9 stand alone), so any changed digit fails.
    text = digits[:pos] + replacement + digits[pos + 1 :]
    assert not terms_match(text, parse_query(digits))


@given(text=text_strategy, word=word_strategy)
@PROFILE
def test_first_match_span_is_within_bounds(text, word):
    term = Term((word,))
    span = first_match_span(text, term)
    if span is None:
        return
    start, end = span
    assert 0 <= start < end <= len(text)
    assert text[start:end] != ""
