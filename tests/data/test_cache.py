"""Raw cache: exact bytes, provenance sidecars, corruption, invalidation policy."""

import datetime as dt
import json
from pathlib import Path

import pytest

from gridflex.config import CachePolicy
from gridflex.data.cache import (
    CacheEntryMeta,
    RawCache,
    chunk_is_final,
    chunk_is_fresh,
    index_is_fresh,
    sha256_hex,
)
from gridflex.data.errors import DataIntegrityError

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _meta(
    content: bytes, retrieved: dt.datetime, n_null: int | None = 0
) -> CacheEntryMeta:
    return CacheEntryMeta(
        url="https://smard.test/x",
        retrieved_at_utc=retrieved.isoformat(),
        sha256=sha256_hex(content),
        status_code=200,
        attempts=1,
        n_points=3,
        n_null=n_null,
    )


def test_roundtrip_preserves_exact_bytes(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    path = cache.chunk_path("DE", 4169, "hour", 123)
    content = b'{"series": [[1, 2.5]]}\r\n'  # odd bytes must survive unchanged
    cache.write(path, content, _meta(content, T0))
    got = cache.read(path)
    assert got is not None
    assert got[0] == content
    assert got[1].retrieved_at == T0
    assert path == tmp_path / "smard" / "DE" / "4169" / "hour" / "chunk_123.json"
    assert json.loads(cache.meta_path(path).read_text())["sha256"] == sha256_hex(
        content
    )


def test_absent_entry_returns_none(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    assert cache.read(cache.index_path("DE", 1, "hour")) is None


def test_corrupted_payload_detected(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    path = cache.index_path("DE", 4169, "hour")
    cache.write(path, b"original", _meta(b"original", T0))
    path.write_bytes(b"tampered")
    with pytest.raises(DataIntegrityError, match="SHA-256"):
        cache.read(path)


def test_write_rejects_mismatched_meta(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    with pytest.raises(ValueError):
        cache.write(cache.index_path("DE", 1, "hour"), b"a", _meta(b"b", T0))


def test_index_freshness() -> None:
    policy = CachePolicy(index_max_age_minutes=60)
    meta = _meta(b"x", T0)
    assert index_is_fresh(meta, T0 + dt.timedelta(minutes=60), policy)
    assert not index_is_fresh(meta, T0 + dt.timedelta(minutes=61), policy)


def test_chunk_finality_needs_no_nulls_and_lag() -> None:
    policy = CachePolicy(finalisation_lag_days=14)
    chunk_end = T0
    late = T0 + dt.timedelta(days=14)
    assert chunk_is_final(_meta(b"x", late), chunk_end, policy)
    assert not chunk_is_final(
        _meta(b"x", late - dt.timedelta(seconds=1)), chunk_end, policy
    )
    assert not chunk_is_final(_meta(b"x", late, n_null=1), chunk_end, policy)
    assert not chunk_is_final(_meta(b"x", late, n_null=None), chunk_end, policy)


def test_open_chunk_expires_final_chunk_does_not() -> None:
    policy = CachePolicy(open_chunk_max_age_minutes=360, finalisation_lag_days=14)
    chunk_end = T0
    open_meta = _meta(b"x", T0 + dt.timedelta(days=1))  # retrieved 1 day after end
    final_meta = _meta(b"x", T0 + dt.timedelta(days=20))
    much_later = T0 + dt.timedelta(days=400)
    assert chunk_is_fresh(
        open_meta, chunk_end, T0 + dt.timedelta(days=1, hours=6), policy
    )
    assert not chunk_is_fresh(open_meta, chunk_end, much_later, policy)
    assert chunk_is_fresh(final_meta, chunk_end, much_later, policy)
