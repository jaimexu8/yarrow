"""LocalStorageProvider.list_objects, used by the future orphan sweep."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from yarrow_storage import LocalStorageProvider, StoredObject, storage_settings


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "objects"
    monkeypatch.setattr(storage_settings, "LOCAL_STORAGE_DIR", str(root))
    return root


def _write(root: Path, key: str, data: bytes = b"data") -> Path:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_list_objects_filters_by_prefix(storage_root: Path):
    _write(storage_root, "documents/one/original")
    _write(storage_root, "documents/two/original")
    _write(storage_root, "uploads/three/0")
    _write(storage_root, "misc.txt")
    storage = LocalStorageProvider()

    def keys(prefix: str) -> set[str]:
        return {obj.key for obj in storage.list_objects(prefix)}

    assert keys("documents/") == {"documents/one/original", "documents/two/original"}
    assert keys("uploads/") == {"uploads/three/0"}
    assert keys("documents/one") == {"documents/one/original"}
    assert keys("missing/") == set()
    assert keys("") == {
        "documents/one/original",
        "documents/two/original",
        "uploads/three/0",
        "misc.txt",
    }


def test_list_objects_uses_forward_slash_keys_for_nested_files(storage_root: Path):
    _write(storage_root, "documents/id/thumbnails/1.webp")
    _write(storage_root, "documents/id/thumbnails/deeper/2.webp")
    storage = LocalStorageProvider()

    keys = sorted(obj.key for obj in storage.list_objects("documents/"))

    assert keys == [
        "documents/id/thumbnails/1.webp",
        "documents/id/thumbnails/deeper/2.webp",
    ]


def test_list_objects_reports_size_and_utc_mtime(storage_root: Path):
    expected_time = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
    path = _write(storage_root, "documents/id/original", b"hello world")
    timestamp = expected_time.timestamp()
    os.utime(path, (timestamp, timestamp))
    storage = LocalStorageProvider()

    [obj] = storage.list_objects("documents/")

    assert isinstance(obj, StoredObject)
    assert obj.key == "documents/id/original"
    assert obj.size == len(b"hello world")
    assert obj.last_modified == expected_time
    assert obj.last_modified.utcoffset() == timedelta(0)
