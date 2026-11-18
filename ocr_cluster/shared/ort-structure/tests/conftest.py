"""Test setup for the ORT structure service.

The package lives in ``ocr_cluster/shared/ort-structure/ort_structure/``; the
directory name contains a hyphen, so it is not on the import path by default.
These tests run with no installed distribution (the cpu gate image sets
PYTHONPATH, and this conftest adds the parent dir as a fallback).

The ``pipeline`` fixture is only active when ORT_GOLDEN_DIR
and ORT_MODELS_DIR are set. i.e. inside the cpu gate image. Outside it, the
gate tests are skipped at collection and the fixture is never requested.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir),
)
from ort_structure.pipeline import Pipeline  # noqa: E402

GOLDEN_DIR = os.environ.get("ORT_GOLDEN_DIR", "")
MODELS_DIR = os.environ.get("ORT_MODELS_DIR", "")


def _require_golden() -> None:
    if not GOLDEN_DIR or not MODELS_DIR:
        pytest.skip(
            "ORT golden gate not enabled: set ORT_GOLDEN_DIR and ORT_MODELS_DIR "
            "(the cpu/Dockerfile image sets both)"
        )


@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    """A CPU EP pipeline. Module-scoped so the five models load once per test
    module. Skips (via _require_golden) when the gate inputs are not mounted.
    """
    _require_golden()
    return Pipeline(model_dir=MODELS_DIR, providers=["CPUExecutionProvider"], assert_gpu=False)
