# Gateway golden corpus

## What this is

Sixteen baseline responses recorded from the paddlex OCR gateway on
2026-11-22, before the `paddleocr-vl-api` service was removed. The inputs are
the twelve image fixtures in `worker/tests/integration/test_docs/` and the
first page of four PDFs, rasterized with PyMuPDF at zoom 2.0. Each response is
one JSON file in this directory.

## What each file contains

Every file has these top-level keys: `file`, `bytes`, `sha256`, `kind`,
`detected_extension`, `source_page`, `expected_pages`, `recorded_structure`,
and `expected`. `file` is the fixture name, `kind` is `image` or
`pdf_first_page`, and `bytes` is the size of the submitted input. The `sha256`
field is the digest of the bytes actually submitted, so the test can detect if
the input has changed.

`recorded_structure` is the response from an independent ort-structure call.
It holds `parsing_res_list`, `table_res_list`, `overall_ocr_res`, `width`, and
`height`.

`expected` is the normalized gateway output. It holds `width`, `height`,
`blocks`, `table_res_list`, and `markdown`. Each block keeps `block_label`,
`block_bbox`, `block_content`, `block_order`, `block_id`, and `block_image`
when present. `markdown` holds `text`, `markdown_images_count`,
`markdown_images_keys`, and `markdown_images_sha256`. Any base64 image data is
replaced with `sha256:<hex>`, so no raw image bytes are stored here.

## How the test uses it

`backend/tests/test_gateway_golden.py` feeds the recorded structure response
and the recorded VLM answers back through the direct gateway using
`httpx.MockTransport`. It needs no GPU and no running OCR stack. It compares
the normalized response to `expected` exactly.

The capture stored the final HTML table text, not the raw OTSL the model
emitted. The test therefore does not exercise OTSL conversion; property tests
cover that.

## Running the test

    pytest backend/tests/test_gateway_golden.py

The four PDF entries are rasterized at test time, so PyMuPDF must be
installed. It is a dev dependency in `backend/pyproject.toml`.

## Regenerating the corpus

This corpus is a fixed baseline, not a CI-generated artifact. Regenerating it
requires restoring the pre-removal pipeline gateway from git history (the
commit before `bae922c`) and a capture tool. That tool is deliberately not
tracked in this repository because it cannot run against the final tree.
