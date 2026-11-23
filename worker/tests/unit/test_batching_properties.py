"""Property tests for batching and the parsing gate.

Bounded so each run stays quick: the point is to catch an off-by-one in the
chunking or a count-based gate, not to search a large space.
"""

from hypothesis import HealthCheck, Phase, given
from hypothesis import settings as hypo_settings
from hypothesis import strategies as st
from yarrow_db.models import Job, JobPage

from app.tasks import ingestion
from app.tasks.ingestion import _utcnow, ingest_document

PARSE = "app.tasks.ingestion.parse_page"
PREPARE = "app.tasks.ingestion.prepare_pages"

PAGE_COUNTS = st.integers(min_value=1, max_value=60)
BATCH_SIZES = st.integers(min_value=1, max_value=30)
RUNNER = hypo_settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    # Fail fast at this commit: shrinking would re-run heavy ingest examples.
    phases=(Phase.generate,),
)


def _pin_batch_size(monkeypatch, size):
    try:
        monkeypatch.setattr(
            ingestion.settings, "INGEST_PAGE_BATCH_SIZE", size, raising=False
        )
    except ValueError:
        monkeypatch.setattr(
            ingestion.settings,
            "__dict__",
            {**vars(ingestion.settings), "INGEST_PAGE_BATCH_SIZE": size},
        )


def _batches(enqueue):
    return [list(args[1]) for name, _queue, args in enqueue if name == PREPARE]


@RUNNER
@given(page_count=PAGE_COUNTS, batch_size=BATCH_SIZES)
def test_full_run_batches_partition_the_run(
    sessions, storage, gateway, enqueue, seed, monkeypatch, page_count, batch_size
):
    _pin_batch_size(monkeypatch, batch_size)
    enqueue.clear()
    storage.store.clear()
    gateway.configure(pages=page_count)
    document, job = seed(page_count=page_count)
    storage.store[document.storage_key] = b"file"

    ingest_document(str(job.id))

    batches = _batches(enqueue)
    flat = [number for batch in batches for number in batch]
    assert sorted(flat) == list(range(1, page_count + 1))
    assert len(flat) == len(set(flat))
    assert all(len(batch) <= batch_size for batch in batches)
    assert all(len(batch) == batch_size for batch in batches[:-1])
    assert not any(name == PARSE for name, _queue, _args in enqueue)


@RUNNER
@given(
    page_count=PAGE_COUNTS,
    batch_size=BATCH_SIZES,
    chosen=st.lists(st.integers(min_value=1, max_value=60), unique=True, max_size=8),
)
def test_partial_run_batches_cover_exactly_the_run(
    sessions,
    storage,
    gateway,
    enqueue,
    seed,
    monkeypatch,
    page_count,
    batch_size,
    chosen,
):
    _pin_batch_size(monkeypatch, batch_size)
    enqueue.clear()
    storage.store.clear()
    gateway.configure(pages=page_count)
    document, job = seed(page_count=page_count)
    storage.store[document.storage_key] = b"file"
    run = sorted({number for number in chosen if number <= page_count})

    ingest_document(str(job.id), pages=run)

    batches = _batches(enqueue)
    flat = [number for batch in batches for number in batch]
    assert sorted(flat) == run
    assert len(flat) == len(set(flat))
    assert all(len(batch) <= batch_size for batch in batches)
    assert all(len(batch) == batch_size for batch in batches[:-1])


@RUNNER
@given(
    page_count=st.integers(min_value=1, max_value=20),
    queued=st.lists(st.integers(min_value=1, max_value=20), unique=True, max_size=20),
)
def test_gate_fires_exactly_when_queued_set_equals_run_set(
    sessions, storage, gateway, enqueue, redis_stub, seed, page_count, queued
):
    enqueue.clear()
    gateway.configure(pages=page_count)
    document, job = seed(page_count=page_count, status="processing")
    storage.store[document.storage_key] = b"file"
    run = set(range(1, page_count + 1))
    queued = {number for number in queued if number in run}
    with sessions() as session:
        job = session.get(Job, job.id)
        job.status = "processing"
        job.document.status = "processing"
        job.current_stage = "preparing"
        job.total_pages = page_count
        session.add_all(
            [
                JobPage(
                    job_id=job.id,
                    page_number=number,
                    status="queued" if number in queued else "processing",
                    queued_at=_utcnow(),
                )
                for number in run
            ]
        )

    ingestion._maybe_start_parsing(str(job.id))

    parse_enqueues = [args[1] for name, _queue, args in enqueue if name == PARSE]
    assert (parse_enqueues != []) == (queued == run)
    if queued == run:
        assert sorted(parse_enqueues) == sorted(run)
        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "parsing"
    else:
        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "preparing"
