"""SMARD client against a synthetic source: download, cache, offline, errors."""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from gridflex.config import CachePolicy
from gridflex.data.errors import DataUnavailableError, SourceResponseError
from tests_helpers import (
    BASE_URL,
    FakeClock,
    FakeResponse,
    FakeSession,
    ForbiddenSession,
    local_monday_starts,
    make_client,
    ms,
    synthetic_source,
)

SID = 4169
# Three weekly chunks around the 2024 autumn DST change (Sunday 27 Oct):
# Mon 21 Oct (169 h), Mon 28 Oct, Mon 4 Nov.
STARTS = local_monday_starts(dt.date(2024, 10, 21), 3)


def _value(t: pd.Timestamp) -> float:
    return float(t.hour)


@pytest.fixture
def source() -> tuple[FakeSession, dict[int, bytes]]:
    session = FakeSession()
    payloads = synthetic_source(session, SID, "hour", STARTS, _value)
    return session, payloads


def test_chunks_follow_local_weeks_incl_169h_dst_week() -> None:
    assert (STARTS[1] - STARTS[0]) == pd.Timedelta(hours=169)
    assert (STARTS[2] - STARTS[1]) == pd.Timedelta(hours=168)


def test_fetch_downloads_and_preserves_raw(tmp_path: Path, source) -> None:
    session, payloads = source
    clock = FakeClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    client = make_client(tmp_path, session, clock)
    start, end = STARTS[0] + pd.Timedelta(hours=160), STARTS[1] + pd.Timedelta(hours=5)
    raw = client.fetch(SID, "hour", start, end)

    assert len(raw.values) == 14  # 9 h of week 1 (169 h) + 5 h of week 2
    assert raw.values.index[0] == start and raw.values.index[-1] == end - pd.Timedelta(
        hours=1
    )
    assert str(raw.values.index.tz) == "UTC"
    assert [c.chunk_start_utc for c in raw.chunks] == [STARTS[0], STARTS[1]]
    assert raw.chunks[0].chunk_end_utc == STARTS[1]  # ends where the next begins
    assert all(c.downloaded for c in raw.chunks)
    # Raw payload stored byte-for-byte with provenance.
    stored = client.cache.chunk_path("DE", SID, "hour", ms(STARTS[0]))
    assert stored.read_bytes() == payloads[ms(STARTS[0])]
    meta = json.loads(client.cache.meta_path(stored).read_text())
    assert meta["retrieved_at_utc"] == "2026-01-01T00:00:00+00:00"
    assert meta["n_points"] == 169 and meta["n_null"] == 0
    assert meta["url"].startswith(BASE_URL)


def test_second_fetch_served_from_cache(tmp_path: Path, source) -> None:
    session, _ = source
    clock = FakeClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    client = make_client(tmp_path, session, clock)
    client.fetch(SID, "hour", STARTS[0], STARTS[1])
    n_calls = len(session.calls)
    clock.advance(minutes=30)
    raw = client.fetch(SID, "hour", STARTS[0], STARTS[1])
    assert len(session.calls) == n_calls
    assert not any(c.downloaded for c in raw.chunks)
    assert raw.chunks[0].final  # retrieved > 14 days after the chunk ended, no nulls


def test_index_refetched_after_max_age_final_chunks_never(
    tmp_path: Path, source
) -> None:
    session, _ = source
    clock = FakeClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    client = make_client(tmp_path, session, clock)
    client.fetch(SID, "hour", STARTS[0], STARTS[1])
    clock.advance(days=365)
    session.calls.clear()
    client.fetch(SID, "hour", STARTS[0], STARTS[1])
    assert session.calls == [f"{BASE_URL}/{SID}/DE/index_hour.json"]


def test_open_chunk_with_nulls_is_refetched(tmp_path: Path) -> None:
    session = FakeSession()
    cutoff = STARTS[2] + pd.Timedelta(hours=50)
    synthetic_source(
        session, SID, "hour", STARTS, lambda t: None if t >= cutoff else 1.0
    )
    clock = FakeClock(STARTS[2].to_pydatetime() + dt.timedelta(hours=60))
    client = make_client(
        tmp_path, session, clock, policy=CachePolicy(open_chunk_max_age_minutes=360)
    )
    raw = client.fetch(SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(days=7))
    assert raw.values.isna().sum() == 168 - 50  # nulls preserved as NaN, not dropped
    assert len(raw.values) == 168
    assert not raw.chunks[0].final

    clock.advance(hours=1)
    session.calls.clear()
    client.fetch(SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(days=7))
    assert len(session.calls) == 0  # still fresh

    clock.advance(hours=6)
    session.calls.clear()
    client.fetch(SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(days=7))
    assert any("_hour_" in u for u in session.calls)  # open chunk re-downloaded


def test_refresh_forces_download(tmp_path: Path, source) -> None:
    session, _ = source
    client = make_client(tmp_path, session, FakeClock())
    client.fetch(SID, "hour", STARTS[0], STARTS[1])
    session.calls.clear()
    raw = client.fetch(SID, "hour", STARTS[0], STARTS[1], refresh=True)
    assert len(session.calls) == 2 and raw.chunks[0].downloaded


def test_offline_uses_cache_without_network(tmp_path: Path, source) -> None:
    session, _ = source
    clock = FakeClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    make_client(tmp_path, session, clock).fetch(SID, "hour", STARTS[0], STARTS[2])
    offline = make_client(tmp_path, ForbiddenSession(), clock, offline=True)
    raw = offline.fetch(SID, "hour", STARTS[0], STARTS[2])
    assert len(raw.values) == 169 + 168


def test_offline_missing_chunk_is_a_clear_error(tmp_path: Path, source) -> None:
    session, _ = source
    clock = FakeClock()
    make_client(tmp_path, session, clock).fetch(SID, "hour", STARTS[0], STARTS[1])
    offline = make_client(tmp_path, None, clock, offline=True)
    with pytest.raises(DataUnavailableError, match="offline mode: 1 chunk"):
        offline.fetch(SID, "hour", STARTS[0], STARTS[2])
    with pytest.raises(DataUnavailableError, match="index"):
        offline.fetch(999, "hour", STARTS[0], STARTS[1])


def test_offline_stale_chunk_is_flagged(tmp_path: Path) -> None:
    session = FakeSession()
    synthetic_source(session, SID, "hour", STARTS, lambda t: 1.0)
    clock = FakeClock(
        STARTS[2].to_pydatetime() + dt.timedelta(hours=2)
    )  # chunk 3 still open
    make_client(tmp_path, session, clock).fetch(
        SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(hours=1)
    )
    clock.advance(days=2)
    raw = make_client(tmp_path, None, clock, offline=True).fetch(
        SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(hours=1)
    )
    assert raw.chunks[0].stale


def test_window_outside_coverage(tmp_path: Path, source) -> None:
    session, _ = source
    client = make_client(tmp_path, session, FakeClock())
    with pytest.raises(DataUnavailableError, match="covers"):
        client.fetch(SID, "hour", STARTS[0] - pd.Timedelta(hours=1), STARTS[1])
    with pytest.raises(DataUnavailableError, match="covers"):
        client.fetch(SID, "hour", STARTS[2], STARTS[2] + pd.Timedelta(days=8))


def test_rejects_naive_or_reversed_windows(tmp_path: Path, source) -> None:
    session, _ = source
    client = make_client(tmp_path, session, FakeClock())
    with pytest.raises(ValueError, match="timezone-aware"):
        client.fetch(SID, "hour", "2024-10-22", "2024-10-23")
    with pytest.raises(ValueError, match="before"):
        client.fetch(SID, "hour", STARTS[1], STARTS[0])


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"series": "x"}',
        b'{"series": [[1]]}',
        b'{"series": [[2, 1], [1, 1]]}',
        b'{"series": [[1, true]]}',
    ],
)
def test_malformed_chunk_rejected_and_not_cached(
    tmp_path: Path, source, body: bytes
) -> None:
    session, _ = source
    url = f"{BASE_URL}/{SID}/DE/{SID}_DE_hour_{ms(STARTS[0])}.json"
    session.routes[url] = [FakeResponse(200, body)]
    client = make_client(tmp_path, session, FakeClock())
    with pytest.raises(SourceResponseError):
        client.fetch(SID, "hour", STARTS[0], STARTS[1])
    assert not client.cache.chunk_path("DE", SID, "hour", ms(STARTS[0])).exists()


@pytest.mark.parametrize(
    "body",
    [b'{"timestamps": []}', b'{"timestamps": [3, 2]}', b"{}", b'{"timestamps": ["a"]}'],
)
def test_malformed_index_rejected(tmp_path: Path, body: bytes) -> None:
    session = FakeSession(
        {f"{BASE_URL}/{SID}/DE/index_hour.json": [FakeResponse(200, body)]}
    )
    client = make_client(tmp_path, session, FakeClock())
    with pytest.raises(SourceResponseError):
        client.fetch(SID, "hour", STARTS[0], STARTS[1])
