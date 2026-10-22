"""SMARD.de chart-data adapter (Bundesnetzagentur | SMARD.de, CC BY 4.0).

Endpoint contract (verified 2026-10-22 for series 4169, 411, 125, 123, 3791 at
both resolutions)::

    {base}/{id}/{region}/index_{resolution}.json
        -> {"timestamps": [epoch_ms, ...]}           chunk starts, ascending
    {base}/{id}/{region}/{id}_{region}_{resolution}_{chunk_start_ms}.json
        -> {"meta_data": {...}, "series": [[epoch_ms, value | null], ...]}

``resolution`` is ``hour`` or ``quarterhour``. Chunks start at local Monday
00:00 and therefore span 167, 168 or 169 hours; a chunk ends where the next
one starts. Timestamps are UTC epoch milliseconds of interval starts. Null
values mark intervals without data (for example, the future part of the
current week) and are preserved as NaN; nothing is dropped or filled here.

This module returns *raw* values with provenance. Unit conversion, duplicate
handling and validation happen in :mod:`gridflex.data.clean` and
:mod:`gridflex.data.validate`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd
import requests

from gridflex.config.models import CachePolicy, DataConfig, Resolution
from gridflex.data.cache import (
    CacheEntryMeta,
    RawCache,
    chunk_is_final,
    chunk_is_fresh,
    index_is_fresh,
    sha256_hex,
)
from gridflex.data.errors import (
    DataUnavailableError,
    SourceResponseError,
)
from gridflex.data.http import FetchResult, HttpClient, SessionLike, utc_now
from gridflex.data.timeutils import require_utc

logger = logging.getLogger(__name__)

_WEEK = dt.timedelta(days=7)


@dataclass(frozen=True)
class ChunkRecord:
    """Provenance of one weekly chunk used to answer a request."""

    chunk_start_utc: dt.datetime
    chunk_end_utc: dt.datetime
    path: str
    meta: CacheEntryMeta
    downloaded: bool
    final: bool
    stale: bool


@dataclass(frozen=True)
class RawSeries:
    """Raw SMARD values in ``[window_start_utc, window_end_utc)`` with provenance.

    ``values`` has a UTC ``DatetimeIndex`` (interval starts), raw units, NaN
    where SMARD served null, and may contain duplicate timestamps if chunks
    overlap (handled by cleaning).
    """

    series_id: int
    region: str
    resolution: Resolution
    window_start_utc: pd.Timestamp
    window_end_utc: pd.Timestamp
    values: pd.Series
    index_meta: CacheEntryMeta
    chunks: tuple[ChunkRecord, ...]


class SmardClient:
    """Fetch SMARD series through a raw cache, online or strictly offline."""

    def __init__(
        self,
        cache: RawCache,
        *,
        http: HttpClient | None,
        base_url: str,
        region: str,
        policy: CachePolicy,
        offline: bool,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        if not offline and http is None:
            raise ValueError("an HttpClient is required unless offline=True")
        self.cache = cache
        self._http = http
        self._base_url = base_url.rstrip("/")
        self.region = region
        self._policy = policy
        self.offline = offline
        self._clock = clock

    @classmethod
    def from_config(
        cls,
        data_cfg: DataConfig,
        *,
        cache_dir: str | Path | None = None,
        offline: bool | None = None,
        session: SessionLike | None = None,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> SmardClient:
        """Build a client from :class:`DataConfig`; arguments override the config."""
        is_offline = data_cfg.offline if offline is None else offline
        http = None
        if not is_offline:
            transport: SessionLike = (
                session
                if session is not None
                else cast(SessionLike, requests.Session())
            )
            http = HttpClient(transport, data_cfg.http, clock=clock)
        return cls(
            RawCache(cache_dir if cache_dir is not None else data_cfg.cache_dir),
            http=http,
            base_url=data_cfg.smard_base_url,
            region=data_cfg.smard_region,
            policy=data_cfg.cache,
            offline=is_offline,
            clock=clock,
        )

    # ------------------------------------------------------------------ #
    # URLs                                                                #
    # ------------------------------------------------------------------ #
    def index_url(self, series_id: int, resolution: str) -> str:
        return f"{self._base_url}/{series_id}/{self.region}/index_{resolution}.json"

    def chunk_url(self, series_id: int, resolution: str, chunk_start_ms: int) -> str:
        return (
            f"{self._base_url}/{series_id}/{self.region}/"
            f"{series_id}_{self.region}_{resolution}_{chunk_start_ms}.json"
        )

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def fetch(
        self,
        series_id: int,
        resolution: Resolution,
        start_utc: pd.Timestamp | dt.datetime | str,
        end_utc: pd.Timestamp | dt.datetime | str,
        *,
        refresh: bool = False,
    ) -> RawSeries:
        """Return raw values for ``[start_utc, end_utc)``.

        Raises :class:`DataUnavailableError` if the window lies outside the
        series' published chunks or (offline) a needed file is not cached.
        """
        start = require_utc(start_utc, "start_utc")
        end = require_utc(end_utc, "end_utc")
        if not start < end:
            raise ValueError(f"start_utc {start} must be before end_utc {end}")
        if self.offline and refresh:
            raise ValueError("refresh=True is not possible in offline mode")

        starts_ms, index_meta = self._index(series_id, resolution, refresh)
        bounds = _chunk_bounds(starts_ms)
        first_start, last_end = bounds[0][0], bounds[-1][1]
        if start < first_start or end > last_end:
            raise DataUnavailableError(
                f"SMARD series {series_id}/{resolution} covers "
                f"[{first_start.isoformat()}, ~{last_end.isoformat()}); requested "
                f"[{start.isoformat()}, {end.isoformat()})"
            )
        needed = [
            (s_ms, c0, c1)
            for s_ms, (c0, c1) in zip(starts_ms, bounds, strict=True)
            if c0 < end and c1 > start
        ]

        records: list[ChunkRecord] = []
        parts: list[pd.Series] = []
        missing: list[str] = []
        for chunk_ms, c0, c1 in needed:
            got = self._chunk(series_id, resolution, chunk_ms, c0, c1, refresh)
            if got is None:
                missing.append(c0.isoformat())
                continue
            record, points = got
            records.append(record)
            parts.append(points)
        if missing:
            raise DataUnavailableError(
                f"offline mode: {len(missing)} chunk(s) of series {series_id}/"
                f"{resolution} not in cache {self.cache.root}: first {missing[0]}"
            )

        values = pd.concat(parts) if parts else pd.Series(dtype="float64")
        values = values[(values.index >= start) & (values.index < end)]
        values.index.name = "timestamp_utc"
        return RawSeries(
            series_id=series_id,
            region=self.region,
            resolution=resolution,
            window_start_utc=start,
            window_end_utc=end,
            values=values.rename(f"smard_{series_id}"),
            index_meta=index_meta,
            chunks=tuple(records),
        )

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
    def _download(self, url: str) -> FetchResult:
        assert self._http is not None
        logger.info("event=smard_download url=%s", url)
        return self._http.get(url)

    def _index(
        self, series_id: int, resolution: str, refresh: bool
    ) -> tuple[list[int], CacheEntryMeta]:
        path = self.cache.index_path(self.region, series_id, resolution)
        cached = self.cache.read(path)
        now = self._clock()
        if self.offline:
            if cached is None:
                raise DataUnavailableError(
                    f"offline mode: index for series {series_id}/{resolution} "
                    f"not in cache {self.cache.root}"
                )
            content, meta = cached
        elif (
            cached is not None
            and not refresh
            and index_is_fresh(cached[1], now, self._policy)
        ):
            content, meta = cached
        else:
            result = self._download(self.index_url(series_id, resolution))
            content = result.content
            meta = _meta(result)
            starts = _parse_index(content, result.url)  # validate before caching
            self.cache.write(path, content, meta)
            return starts, meta
        return _parse_index(content, str(path)), meta

    def _chunk(
        self,
        series_id: int,
        resolution: str,
        chunk_ms: int,
        chunk_start: pd.Timestamp,
        chunk_end: pd.Timestamp,
        refresh: bool,
    ) -> tuple[ChunkRecord, pd.Series] | None:
        path = self.cache.chunk_path(self.region, series_id, resolution, chunk_ms)
        cached = self.cache.read(path)
        now = self._clock()
        end_dt = chunk_end.to_pydatetime()
        downloaded = False
        if self.offline:
            if cached is None:
                return None
            content, meta = cached
        elif (
            cached is not None
            and not refresh
            and chunk_is_fresh(cached[1], end_dt, now, self._policy)
        ):
            content, meta = cached
        else:
            result = self._download(self.chunk_url(series_id, resolution, chunk_ms))
            content = result.content
            points_new, created = _parse_chunk(content, result.url)
            meta = _meta(
                result,
                n_points=len(points_new),
                n_null=int(points_new.isna().sum()),
                source_created=created,
            )
            self.cache.write(path, content, meta)
            downloaded = True
        points, _ = _parse_chunk(content, str(path))
        final = chunk_is_final(meta, end_dt, self._policy)
        stale = not chunk_is_fresh(meta, end_dt, now, self._policy)
        if stale:
            logger.warning(
                "event=stale_chunk series=%s resolution=%s chunk_start=%s retrieved=%s",
                series_id,
                resolution,
                chunk_start.isoformat(),
                meta.retrieved_at_utc,
            )
        record = ChunkRecord(
            chunk_start_utc=chunk_start.to_pydatetime(),
            chunk_end_utc=end_dt,
            path=str(path),
            meta=meta,
            downloaded=downloaded,
            final=final,
            stale=stale,
        )
        return record, points


# --------------------------------------------------------------------------- #
# Parsing helpers                                                             #
# --------------------------------------------------------------------------- #
def _meta(
    result: FetchResult,
    *,
    n_points: int | None = None,
    n_null: int | None = None,
    source_created: str | None = None,
) -> CacheEntryMeta:
    return CacheEntryMeta(
        url=result.url,
        retrieved_at_utc=result.retrieved_at.astimezone(dt.UTC).isoformat(),
        sha256=sha256_hex(result.content),
        status_code=result.status_code,
        attempts=result.attempts,
        n_points=n_points,
        n_null=n_null,
        source_created_utc=source_created,
    )


def _load_json(content: bytes, where: str) -> Any:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceResponseError(f"invalid JSON from {where}: {exc}") from exc


def _parse_index(content: bytes, where: str) -> list[int]:
    payload = _load_json(content, where)
    stamps = payload.get("timestamps") if isinstance(payload, dict) else None
    if (
        not isinstance(stamps, list)
        or not stamps
        or not all(isinstance(t, int) and not isinstance(t, bool) for t in stamps)
    ):
        raise SourceResponseError(
            f"{where}: expected non-empty integer 'timestamps' list"
        )
    if stamps != sorted(set(stamps)):
        raise SourceResponseError(f"{where}: index timestamps not strictly increasing")
    return list(stamps)


def _parse_chunk(content: bytes, where: str) -> tuple[pd.Series, str | None]:
    payload = _load_json(content, where)
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        raise SourceResponseError(f"{where}: expected a 'series' list")
    stamps: list[int] = []
    values: list[float] = []
    for item in series:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], int)
            or not (item[1] is None or isinstance(item[1], int | float))
            or isinstance(item[1], bool)
        ):
            raise SourceResponseError(f"{where}: malformed point {item!r}")
        value = math.nan if item[1] is None else float(item[1])
        stamps.append(item[0])
        values.append(value)
    if stamps != sorted(stamps):
        raise SourceResponseError(f"{where}: chunk timestamps not ascending")
    index = pd.DatetimeIndex(
        pd.to_datetime(stamps, unit="ms", utc=True), name="timestamp_utc"
    )
    created = None
    meta = payload.get("meta_data")
    if isinstance(meta, dict) and isinstance(meta.get("created"), int):
        created = dt.datetime.fromtimestamp(meta["created"] / 1000, dt.UTC).isoformat()
    return pd.Series(values, index=index, dtype="float64"), created


def _chunk_bounds(starts_ms: list[int]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(start, end) per chunk; each chunk ends where the next begins."""
    starts = [pd.Timestamp(ms, unit="ms", tz="UTC") for ms in starts_ms]
    ends = [*starts[1:], starts[-1] + _WEEK]
    return list(zip(starts, ends, strict=True))
