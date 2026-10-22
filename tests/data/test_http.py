"""HTTP client: timeouts passed through, bounded retries, backoff, pacing."""

import pytest
import requests

from gridflex.config import HttpConfig
from gridflex.data.errors import SourceNotFoundError, SourceUnavailableError
from gridflex.data.http import HttpClient
from tests_helpers import FakeClock, FakeResponse, FakeSession

URL = "https://smard.test/x.json"


def _client(session: FakeSession, sleeps: list[float], **cfg: float) -> HttpClient:
    config = HttpConfig(**{"min_interval_s": 0.0, "backoff_s": 1.0, **cfg})
    return HttpClient(session, config, sleep=sleeps.append, clock=FakeClock())


def test_success_first_try_passes_timeout() -> None:
    s = FakeSession({URL: [FakeResponse(200, b"{}")]})
    sleeps: list[float] = []
    result = _client(s, sleeps, timeout_s=7.5).get(URL)
    assert result.content == b"{}" and result.attempts == 1
    assert s.timeouts == [7.5]
    assert sleeps == []


def test_retries_transient_failures_with_exponential_backoff() -> None:
    s = FakeSession(
        {
            URL: [
                requests.Timeout("slow"),
                requests.ConnectionError("reset"),
                FakeResponse(503),
                FakeResponse(200, b"ok"),
            ]
        }
    )
    sleeps: list[float] = []
    result = _client(s, sleeps, max_retries=3, backoff_s=1.0).get(URL)
    assert result.content == b"ok"
    assert result.attempts == 4
    assert sleeps == [1.0, 2.0, 4.0]  # 1 * 2**(attempt-1)


def test_backoff_is_capped() -> None:
    s = FakeSession({URL: [FakeResponse(500)] * 4 + [FakeResponse(200, b"ok")]})
    sleeps: list[float] = []
    _client(s, sleeps, max_retries=4, backoff_s=10.0, backoff_max_s=15.0).get(URL)
    assert sleeps == [10.0, 15.0, 15.0, 15.0]


def test_gives_up_after_bounded_attempts() -> None:
    s = FakeSession({URL: [FakeResponse(429)]})
    sleeps: list[float] = []
    with pytest.raises(SourceUnavailableError, match="after 3 attempt"):
        _client(s, sleeps, max_retries=2).get(URL)
    assert len(s.calls) == 3
    assert len(sleeps) == 2


def test_404_is_not_retried() -> None:
    s = FakeSession({URL: [FakeResponse(404)]})
    with pytest.raises(SourceNotFoundError):
        _client(s, []).get(URL)
    assert len(s.calls) == 1


def test_other_client_errors_are_not_retried() -> None:
    s = FakeSession({URL: [FakeResponse(403)]})
    with pytest.raises(SourceUnavailableError, match="HTTP 403"):
        _client(s, []).get(URL)
    assert len(s.calls) == 1


def test_requests_are_paced() -> None:
    s = FakeSession({URL: [FakeResponse(200, b"ok")]})
    sleeps: list[float] = []
    ticks = iter([0.0, 0.01, 0.06])  # 1st stamp, 2nd elapsed check, 2nd stamp
    client = HttpClient(
        s,
        HttpConfig(min_interval_s=0.05),
        sleep=sleeps.append,
        monotonic=lambda: next(ticks),
        clock=FakeClock(),
    )
    client.get(URL)
    client.get(URL)
    assert sleeps == [pytest.approx(0.04)]
