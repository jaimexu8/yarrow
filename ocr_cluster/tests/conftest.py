"""pytest configuration for the OCR cluster integration tests.

Registers the ``ocr_cluster`` marker so the build/run tests can be selected or
deselected explicitly (``pytest -m ocr_cluster`` / ``-m "not ocr_cluster"``)
without tripping ``--strict-markers``.
"""

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ocr_cluster: builds and runs the OCR cluster (non-VLM services); "
        "requires docker and is gated behind OCR_CLUSTER_TEST=1",
    )
