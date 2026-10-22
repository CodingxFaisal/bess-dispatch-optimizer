"""Small HTTP GET client with timeouts, bounded retries and request pacing."""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from gridflex.config.models import HttpConfig
from gridflex.data.errors import SourceNotFoundError, SourceUnavailableError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class SessionLike(Protocol):
    """The subset of :class:`requests.Session` this client uses."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class FetchResult:
    url: str
    content: bytes
    status_code: int
    retrieved_at: dt.datetime
    attempts: int


class HttpClient:
    """GET with a per-request timeout and at most ``1 + max_retries`` attempts.

    Retries on timeouts, connection errors and HTTP 429/5xx with exponential
    backoff (``backoff_s * 2**(attempt-1)``, capped at ``backoff_max_s``).
    404 raises :class:`SourceNotFoundError`; other 4xx fail immediately.
    """

    def __init__(
        self,
        session: SessionLike,
        config: HttpConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        self._session = session
        self._cfg = config
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock = clock
        self._last_request: float | None = None

    def _pace(self) -> None:
        if self._last_request is not None and self._cfg.min_interval_s > 0:
            wait = self._cfg.min_interval_s - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._monotonic()

    def get(self, url: str) -> FetchResult:
        attempts = 1 + self._cfg.max_retries
        last_problem = "no attempt made"
        for attempt in range(1, attempts + 1):
            self._pace()
            try:
                response = self._session.get(url, timeout=self._cfg.timeout_s)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                status = int(response.status_code)
                if status == 200:
                    logger.debug("event=http_ok url=%s attempts=%d", url, attempt)
                    return FetchResult(
                        url=url,
                        content=bytes(response.content),
                        status_code=status,
                        retrieved_at=self._clock(),
                        attempts=attempt,
                    )
                if status == 404:
                    raise SourceNotFoundError(f"HTTP 404 for {url}")
                if status not in RETRYABLE_STATUS:
                    raise SourceUnavailableError(f"HTTP {status} for {url}")
                last_problem = f"HTTP {status}"
            if attempt < attempts:
                delay = min(
                    self._cfg.backoff_s * 2 ** (attempt - 1), self._cfg.backoff_max_s
                )
                logger.warning(
                    "event=http_retry url=%s attempt=%d problem=%r delay_s=%.2f",
                    url,
                    attempt,
                    last_problem,
                    delay,
                )
                self._sleep(delay)
        raise SourceUnavailableError(
            f"giving up on {url} after {attempts} attempt(s): {last_problem}"
        )
