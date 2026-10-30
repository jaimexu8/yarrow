"""exact-phrase and OCR-tolerant query parsing and matching.
 They pin down the query grammar, the confusable character classes
(1/l/I, 0/O), and the edit-distance tolerance, so the endpoint and the SQL
layer can rely on one definition of what a query means.
"""

import pytest

from app.services.search_query import (
    Term,
    first_match_span,
    parse_query,
    terms_match,
)


def T(words, quoted=False):
    """Build a Term from a plain list for readable assertions."""
    return Term(tuple(words), quoted=quoted)


class TestParseQuery:
    def test_single_word(self):
        assert parse_query("quarterly") == [T(["quarterly"])]

    def test_blank_is_empty(self):
        assert parse_query("") == []
        assert parse_query("   ") == []

    def test_unquoted_multi_word_is_separate_word_terms(self):
        assert parse_query("quarterly summary report") == [
            T(["quarterly"]),
            T(["summary"]),
            T(["report"]),
        ]

    def test_quoted_phrase_is_one_term(self):
        assert parse_query('"quarterly summary"') == [T(["quarterly", "summary"], True)]

    def test_quoted_single_word_is_a_phrase_term(self):
        assert parse_query('"quarterly"') == [T(["quarterly"], True)]

    def test_mixed_quoted_and_unquoted(self):
        assert parse_query('"exact phrase" extra quarterly') == [
            T(["exact", "phrase"], True),
            T(["extra"]),
            T(["quarterly"]),
        ]

    def test_unclosed_quote_runs_to_end(self):
        assert parse_query('"quarterly summary') == [T(["quarterly", "summary"], True)]

    def test_empty_quotes_are_ignored(self):
        assert parse_query('"" quarterly') == [T(["quarterly"])]
        assert parse_query('""') == []

    def test_whitespace_between_terms_is_a_delimiter(self):
        assert parse_query('  "a b"   c  ') == [T(["a", "b"], True), T(["c"])]

    def test_tabs_and_newlines_separate_terms(self):
        assert parse_query("a\tb\nc") == [T(["a"]), T(["b"]), T(["c"])]


class TestConfusable:
    """1/l/I and 0/O are interchangeable in both directions."""

    @pytest.mark.parametrize(
        "text, query",
        [
            ("section l.1 begins", '"section 1.1"'),
            ("section 1.1 begins", '"section l.1"'),
            ("in 2O24 the numbers", "2024"),
            ("in 2024 the numbers", "2O24"),
            ("I am 1000", "i am 1O00"),
        ],
    )
    def test_confusables_match(self, text, query):
        assert terms_match(text, parse_query(query))

    @pytest.mark.parametrize(
        "text, query",
        [
            ("section 2.1 begins", '"section 1.1"'),  # 2 is not a 1/l/I
            ("in 2023 the numbers", "2024"),
        ],
    )
    def test_non_confusable_digits_do_not_match(self, text, query):
        assert not terms_match(text, parse_query(query))


class TestExactPhrase:
    def test_contiguous_phrase_matches(self):
        assert terms_match(
            "the quarterly summary is ready", parse_query('"quarterly summary"')
        )

    def test_phrase_is_case_insensitive(self):
        assert terms_match(
            "The Quarterly Summary is ready", parse_query('"quarterly summary"')
        )

    def test_phrase_allows_extra_whitespace_between_words(self):
        assert terms_match("quarterly   summary", parse_query('"quarterly summary"'))

    def test_phrase_out_of_order_does_not_match(self):
        assert not terms_match("summary quarterly", parse_query('"quarterly summary"'))

    def test_phrase_interleaved_words_do_not_match(self):
        assert not terms_match(
            "quarterly report of the summary", parse_query('"quarterly summary"')
        )

    def test_phrase_metacharacters_are_literal(self):
        assert terms_match("the cost is a.b", parse_query('"a.b"'))
        assert not terms_match("the cost is aXb", parse_query('"a.b"'))

    def test_unquoted_multi_word_matches_non_contiguous(self):
        # Both words present anywhere, in any order; not necessarily adjacent.
        assert terms_match(
            "summary of the quarterly report", parse_query("quarterly summary")
        )

    def test_unquoted_multi_word_requires_all_words(self):
        assert not terms_match("quarterly only", parse_query("quarterly summary"))

    def test_and_across_a_phrase_and_a_word(self):
        text = "the quarterly summary of 2024"
        assert terms_match(text, parse_query('"quarterly summary" 2024'))
        assert not terms_match(
            "the quarterly summary of 2023", parse_query('"quarterly summary" 2024')
        )


class TestFuzzy:
    """OCR typos within one edit (including a transposition) still match."""

    @pytest.mark.parametrize(
        "text, query",
        [
            ("we recieve the goods", "receive"),  # transposition
            ("a receiv error", "receive"),  # missing final letter
            ("teh report", "the"),  # transposition, 3-letter word
        ],
    )
    def test_one_edit_typo_matches(self, text, query):
        assert terms_match(text, parse_query(query))

    @pytest.mark.parametrize(
        "text, query",
        [
            ("we accepted the goods", "receive"),  # different word
            ("totally different words", "quarterly"),
        ],
    )
    def test_far_typo_does_not_match(self, text, query):
        assert not terms_match(text, parse_query(query))

    def test_short_query_word_disables_fuzzy(self):
        # "a" must not fuzzy-match every 1- and 2-letter token.
        assert not terms_match("i 1 l o 0", parse_query("a"))
        # but an exact substring "a" still matches as today.
        assert terms_match("alpha", parse_query("a"))


class TestFirstMatchSpan:
    def test_word_span(self):
        assert first_match_span("the quarterly summary", T(["quarterly"])) == (4, 13)

    def test_phrase_span_covers_all_words(self):
        # "the quarterly summary": quarterly starts at 4, summary ends at 21.
        assert first_match_span(
            "the quarterly summary", T(["quarterly", "summary"], True)
        ) == (4, 21)

    def test_confusable_span_covers_actual_characters(self):
        # The span must cover the OCR'd text ("l.1"), not the query ("1.1"),
        # so the frontend can tell a near match from an exact one.
        span = first_match_span("section l.1 begins", T(["section", "1.1"], True))
        assert span is not None
        assert "section l.1 begins"[span[0] : span[1]] == "section l.1"

    def test_fuzzy_span_covers_the_token(self):
        span = first_match_span("we recieve the goods", T(["receive"]))
        assert span is not None
        assert "we recieve the goods"[span[0] : span[1]] == "recieve"

    def test_no_match_is_none(self):
        assert first_match_span("the quarterly summary", T(["zebra"])) is None

    def test_case_insensitive_span(self):
        assert first_match_span("The Quarterly Summary", T(["quarterly"])) == (4, 13)
