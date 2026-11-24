"""Property-based tests for the pure reprocessing-rule helpers.

Read from the bodies of ``pages_to_process``, ``reprocess_scope``,
``is_interrupted`` and ``is_running``. The candidate
properties are:

* ``pages_to_process`` returns sorted, unique page numbers within
  ``1..total_pages`` (and is exactly the complement of the completed pages).
* ``is_interrupted`` and ``is_running`` are mutually exclusive, and a running
  job is never interrupted.
* ``reprocess_scope`` returns ``None`` while a job is running, and otherwise
  ``"incomplete"`` or ``"all"`` (cross-checked against an independent oracle
  of the decision table).

The existing example tests (``test_reprocess_rules.py``) pin these for a few
concrete inputs; the properties here extend them over the full input space
and add the exact-complement and running-implies-None invariants.
"""

from datetime import datetime, timedelta

from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from app.core.config import settings
from app.services.reprocess import (
    is_interrupted,
    is_running,
    pages_to_process,
    reprocess_scope,
)

# Naive UTC, matching the timestamps the models write
BASE = datetime(2026, 10, 2, 12, 0, 0)

# Seconds the job has been silent, drawn across fresh, exactly-at-limit, and
# stale values.
SILENCE = st.integers(min_value=-2 * 60 * 60, max_value=24 * 60 * 60)

ACTIVE_STATES = ["queued", "processing"]
INACTIVE_STATES = ["completed", "failed", "canceled"]
ALL_STATES = ACTIVE_STATES + INACTIVE_STATES


def naive(seconds: int) -> datetime:
    """A naive UTC timestamp ``seconds`` before the base reference."""
    return (BASE - timedelta(seconds=seconds)).replace(tzinfo=None)


job_strategy = st.builds(
    lambda status, updated: {"status": status, "updated_at": updated},
    status=st.sampled_from(ALL_STATES),
    updated=st.one_of(
        st.just(None),
        SILENCE.map(naive),
    ),
)

now_strategy = SILENCE.map(naive)
page_count_strategy = st.one_of(st.just(None), st.integers(min_value=0, max_value=50))
completed_strategy = st.integers(min_value=0, max_value=60)
status_strategy = st.sampled_from(ALL_STATES + ["unknown"])

PROFILE = hypothesis_settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# --- pages_to_process ---------------------------------------------------------


@given(
    page_count=st.one_of(st.just(None), st.integers(min_value=0, max_value=50)),
    completed=st.lists(
        st.integers(min_value=-5, max_value=60), min_size=0, max_size=20
    ),
)
@PROFILE
def test_pages_to_process_sorted_unique_and_in_range(page_count, completed):
    result = pages_to_process(page_count, completed)
    if result is None:
        assert page_count is None
        return
    assert result == sorted(result)
    assert len(result) == len(set(result))
    for number in result:
        assert 1 <= number <= page_count
    # Exact complement of the completed pages (out-of-range/duplicates in
    # `completed` are ignored).
    expected = [n for n in range(1, (page_count or 0) + 1) if n not in set(completed)]
    assert result == expected


# --- is_interrupted / is_running ---------------------------------------------


@given(job=job_strategy, now=now_strategy)
@PROFILE
def test_interrupted_and_running_are_mutually_exclusive(job, now):
    assert not (is_interrupted(job, now) and is_running(job, now))


@given(job=job_strategy, now=now_strategy)
@PROFILE
def test_running_job_is_never_interrupted(job, now):
    if is_running(job, now):
        assert not is_interrupted(job, now)
    if job["status"] not in ACTIVE_STATES:
        assert not is_running(job, now)


# --- reprocess_scope ----------------------------------------------------------


def _oracle_scope(document_status, page_count, completed_pages, job, now) -> str | None:
    """Independent restatement of the reprocess_scope decision table."""
    job_active = job is not None and job["status"] in ACTIVE_STATES
    is_int = (
        job is not None
        and job["status"] in ACTIVE_STATES
        and (
            job["updated_at"] is None
            or now - job["updated_at"]
            > timedelta(seconds=settings.JOB_INTERRUPTED_AFTER_SECONDS)
        )
    )
    if job_active and not is_int:
        return None
    interrupted = is_int or (document_status in ACTIVE_STATES and not job_active)
    if interrupted or document_status == "failed":
        return "incomplete"
    if (
        document_status == "completed"
        and page_count is not None
        and completed_pages < page_count
    ):
        return "incomplete"
    return "all"


@given(
    document_status=status_strategy,
    page_count=page_count_strategy,
    completed_pages=completed_strategy,
    job=job_strategy,
    now=now_strategy,
)
@PROFILE
def test_reprocess_scope_matches_oracle(
    document_status, page_count, completed_pages, job, now
):
    assert reprocess_scope(
        document_status, page_count, completed_pages, job, now
    ) == _oracle_scope(document_status, page_count, completed_pages, job, now)


@given(
    document_status=status_strategy,
    page_count=page_count_strategy,
    completed_pages=completed_strategy,
    job=job_strategy,
    now=now_strategy,
)
@PROFILE
def test_reprocess_scope_none_only_while_running(
    document_status, page_count, completed_pages, job, now
):
    scope = reprocess_scope(document_status, page_count, completed_pages, job, now)
    if is_running(job, now):
        assert scope is None
    else:
        assert scope in ("incomplete", "all")
