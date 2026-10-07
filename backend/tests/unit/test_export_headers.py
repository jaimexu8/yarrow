"""The filename and Content-Disposition helpers behind the export endpoints."""

from app.services.export import content_disposition, display_stem, safe_stem

FALLBACK = "document-x"


class TestSafeStem:
    def test_strips_extension(self):
        assert safe_stem("report.pdf", FALLBACK) == "report"

    def test_strips_path_separators(self):
        assert safe_stem("a/b/c.txt", FALLBACK) == "c"
        assert safe_stem("C:\\docs\\report.pdf", FALLBACK) == "report"

    def test_keeps_a_name_without_extension(self):
        assert safe_stem("Makefile", FALLBACK) == "Makefile"

    def test_replaces_unsafe_characters(self):
        assert safe_stem("my report (1).pdf", FALLBACK) == "my-report-1"

    def test_caps_the_length_at_80(self):
        assert safe_stem("a" * 200 + ".pdf", FALLBACK) == "a" * 80

    def test_falls_back_when_only_unsafe_characters_remain(self):
        assert safe_stem("( ).pdf", FALLBACK) == FALLBACK
        assert safe_stem(None, FALLBACK) == FALLBACK
        assert safe_stem("", FALLBACK) == FALLBACK


class TestDisplayStem:
    def test_keeps_non_ascii_characters(self):
        assert display_stem("Berík report.pdf", FALLBACK) == "Berík report"

    def test_removes_characters_that_would_break_the_header(self):
        assert display_stem('a"b;c.txt', FALLBACK) == "abc"

    def test_removes_control_characters(self):
        assert display_stem("bad\x01name.pdf", FALLBACK) == "badname"

    def test_caps_the_length_at_80(self):
        assert display_stem("é" * 200 + ".pdf", FALLBACK) == "é" * 80

    def test_falls_back_when_nothing_usable_remains(self):
        assert display_stem('".pdf', FALLBACK) == FALLBACK
        assert display_stem(None, FALLBACK) == FALLBACK


class TestContentDisposition:
    def test_attachment_with_one_name(self):
        assert content_disposition("report.md") == 'attachment; filename="report.md"'

    def test_inline_disposition(self):
        assert (
            content_disposition("report.pdf", disposition="inline")
            == 'inline; filename="report.pdf"'
        )

    def test_carries_the_utf8_form_when_it_differs(self):
        header = content_disposition(
            "Ber-k-report.md",
            unicode_filename="Berík report.md",
        )
        assert 'filename="Ber-k-report.md"' in header
        assert "filename*=UTF-8''Ber%C3%ADk%20report.md" in header

    def test_omits_the_utf8_form_when_the_names_match(self):
        header = content_disposition("report.md", unicode_filename="report.md")
        assert "filename*" not in header
