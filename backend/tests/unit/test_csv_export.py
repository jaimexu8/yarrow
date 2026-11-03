"""CSV table export (US-49). No database."""

from app.services.markdown import render_csv
from tests.factories import page, region, simple_table, tree


def test_one_table_is_a_csv_grid():
    table = simple_table(
        [["Name", "Amount"], ["Rent", "1200"], ["Food", "400"]],
        title="Costs",
    )
    document = tree(
        [
            page(
                1,
                region(
                    "table",
                    table_id=table.id,
                    region_table_id=table.parts[0].region_table_id,
                    is_first_table_part=True,
                ),
            )
        ],
        tables=[table],
    )
    body = render_csv(document)
    assert body.startswith("\ufeff")
    assert "Costs" in body
    assert "Name,Amount" in body
    assert "Rent,1200" in body
    assert "Food,400" in body


def test_every_table_is_in_the_same_file():
    first = simple_table([["A", "B"], ["1", "2"]], title="First")
    second = simple_table([["C", "D"], ["3", "4"]], title="Second")
    document = tree(
        [
            page(
                1,
                region(
                    "table",
                    table_id=first.id,
                    region_table_id=first.parts[0].region_table_id,
                    is_first_table_part=True,
                ),
                region(
                    "table",
                    table_id=second.id,
                    region_table_id=second.parts[0].region_table_id,
                    is_first_table_part=True,
                    reading_order=1,
                ),
            )
        ],
        tables=[first, second],
    )
    body = render_csv(document)
    assert "First" in body
    assert "A,B" in body
    assert "1,2" in body
    assert "Second" in body
    assert "C,D" in body
    assert "3,4" in body
    first_at = body.index("First")
    second_at = body.index("Second")
    assert first_at < second_at


def test_commas_and_quotes_are_escaped():
    table = simple_table([["Item", "Note"], ['He said "hi"', "a, b"]])
    document = tree([page(1, region("table"))], tables=[table])
    body = render_csv(document)
    assert '"He said ""hi"""' in body
    assert '"a, b"' in body


def test_document_with_no_tables_is_still_a_csv():
    document = tree([page(1, region("paragraph", text="hello"))])
    body = render_csv(document)
    assert body == "\ufeff"
