"""Test doubles and helpers shared by the test suite (importable as a module)."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from gridflex.config.models import CachePolicy, HttpConfig
from gridflex.data.cache import RawCache
from gridflex.data.http import HttpClient
from gridflex.data.smard import SmardClient

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"
FIXTURE_CACHE = REPO_ROOT / "tests" / "fixtures" / "smard_cache"
BASE_URL = "https://smard.test/app/chart_data"


@dataclass
class FakeResponse:
    status_code: int
    content: bytes = b""


@dataclass
class FakeClock:
    now: dt.datetime = dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += dt.timedelta(**kwargs)


@dataclass
class FakeSession:
    """Serves queued responses per URL; records every call.

    ``routes[url]`` is a list consumed in order (the last entry repeats); an
    entry may be a :class:`FakeResponse` or an exception instance to raise.
    """

    routes: dict[str, list[FakeResponse | Exception]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def get(self, url: str, **kwargs: float) -> FakeResponse:
        self.calls.append(url)
        self.timeouts.append(kwargs["timeout"])
        queue = self.routes.get(url)
        if not queue:
            return FakeResponse(404, b"not found")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


class ForbiddenSession:
    """Fails the test if any network access is attempted."""

    def get(self, url: str, **kwargs: float) -> FakeResponse:
        raise AssertionError(f"unexpected network access: {url}")


# --------------------------------------------------------------------------- #
# Synthetic SMARD source                                                      #
# --------------------------------------------------------------------------- #
def local_monday_starts(
    first_monday: dt.date, n_weeks: int, tz: str = "Europe/Berlin"
) -> list[pd.Timestamp]:
    return [
        pd.Timestamp(first_monday + dt.timedelta(weeks=i))
        .tz_localize(tz)
        .tz_convert("UTC")
        for i in range(n_weeks)
    ]


def ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def synthetic_source(
    session: FakeSession,
    series_id: int,
    resolution: str,
    chunk_starts: list[pd.Timestamp],
    value: Callable[[pd.Timestamp], float | None],
    region: str = "DE",
) -> dict[int, bytes]:
    """Register index + chunk payloads for a synthetic series; return chunk bytes."""
    step = pd.Timedelta(minutes=60 if resolution == "hour" else 15)
    index = {"timestamps": [ms(t) for t in chunk_starts]}
    session.routes[f"{BASE_URL}/{series_id}/{region}/index_{resolution}.json"] = [
        FakeResponse(200, json.dumps(index).encode())
    ]
    ends = [*chunk_starts[1:], chunk_starts[-1] + pd.Timedelta(days=7)]
    payloads: dict[int, bytes] = {}
    for start, end in zip(chunk_starts, ends, strict=True):
        stamps = pd.date_range(start, end, freq=step, inclusive="left")
        body = {
            "meta_data": {"version": 1, "created": ms(start)},
            "series": [[ms(t), value(t)] for t in stamps],
        }
        content = json.dumps(body).encode()
        payloads[ms(start)] = content
        name = f"{series_id}_{region}_{resolution}_{ms(start)}.json"
        url = f"{BASE_URL}/{series_id}/{region}/{name}"
        session.routes[url] = [FakeResponse(200, content)]
    return payloads


def make_client(
    cache_dir: Path,
    session: FakeSession | ForbiddenSession | None,
    clock: FakeClock,
    *,
    offline: bool = False,
    policy: CachePolicy | None = None,
    http_cfg: HttpConfig | None = None,
    sleeps: list[float] | None = None,
) -> SmardClient:
    sleep_log = sleeps if sleeps is not None else []
    http = None
    if session is not None and not offline:
        http = HttpClient(
            session,
            http_cfg or HttpConfig(min_interval_s=0.0, backoff_s=0.5),
            sleep=sleep_log.append,
            clock=clock,
        )
    return SmardClient(
        RawCache(cache_dir),
        http=http,
        base_url=BASE_URL,
        region="DE",
        policy=policy or CachePolicy(),
        offline=offline,
        clock=clock,
    )
