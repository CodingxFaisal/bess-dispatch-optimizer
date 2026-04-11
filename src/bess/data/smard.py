"""Client for the SMARD.de chart-data API (Bundesnetzagentur / German TSO data).

SMARD publishes German market data as static JSON files, hour-resolution data
chopped into weekly chunks. No registration, no API key, no rate limit worth
worrying about. Two endpoints per series:

  index:  https://www.smard.de/app/chart_data/{id}/{region}/index_hour.json
          -> {"timestamps": [epoch_ms, ...]}  (start of each weekly chunk)

  data:   https://www.smard.de/app/chart_data/{id}/{region}/{id}_{region}_hour_{ts}.json
          -> {"series": [[epoch_ms, value], ...]}  (168 hourly points)

Timestamps are UTC epoch milliseconds. We parse them as UTC and convert to the
market timezone (Europe/Berlin) so DST-length days (23h / 25h) come out right.

Fetched series are cached to ``data/raw/series_{id}.csv`` and merged on
subsequent calls, so a full multi-year pull only hits the network once.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://www.smard.de/app/chart_data"
_WEEK_MS = 7 * 24 * 3600 * 1000
DEFAULT_TZ = "Europe/Berlin"


def _index_url(series_id: int, region: str) -> str:
    return f"{BASE_URL}/{series_id}/{region}/index_hour.json"


def _data_url(series_id: int, region: str, ts_ms: int) -> str:
    return f"{BASE_URL}/{series_id}/{region}/{series_id}_{region}_hour_{ts_ms}.json"


def _cache_path(cache_dir: Path, series_id: int, region: str) -> Path:
    return cache_dir / f"series_{series_id}_{region}.csv"


def _load_cache(path: Path) -> pd.Series:
    if not path.exists():
        return pd.Series(dtype="float64")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    s = df.set_index("timestamp")["value"]
    # CSV round-trips tz as an offset string; normalize back to the market tz.
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(DEFAULT_TZ)
    return s


def _save_cache(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = series.sort_index()
    out.index.name = "timestamp"
    out.rename("value").to_csv(path)


def fetch_series(
    series_id: int,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    region: str = "DE",
    cache_dir: str | Path = "data/raw",
    session: requests.Session | None = None,
    tz: str = DEFAULT_TZ,
    pause_s: float = 0.05,
) -> pd.Series:
    """Fetch one SMARD hourly series over ``[start, end)`` as a tz-aware Series.

    Only weekly chunks not already cached are downloaded; the cache is then
    updated. The returned series is trimmed to the requested window.
    """
    cache_dir = Path(cache_dir)
    start_ts = pd.Timestamp(start, tz=tz)
    end_ts = pd.Timestamp(end, tz=tz)
    start_ms = int(start_ts.tz_convert("UTC").timestamp() * 1000)
    end_ms = int(end_ts.tz_convert("UTC").timestamp() * 1000)

    cache_path = _cache_path(cache_dir, series_id, region)
    cached = _load_cache(cache_path)

    own_session = session is None
    session = session or requests.Session()
    try:
        index = session.get(_index_url(series_id, region), timeout=30).json()
        week_starts = [
            ts for ts in index["timestamps"]
            if (ts + _WEEK_MS) > start_ms and ts < end_ms
        ]

        # Which weeks are already fully covered by the cache? A week is covered
        # if we hold data inside its span; re-fetch partial/missing weeks only.
        have_ms = set()
        if not cached.empty:
            have_ms = set((cached.index.tz_convert("UTC").astype("int64") // 1_000_000))

        new_frames = []
        for ts in week_starts:
            week_end = ts + _WEEK_MS
            week_hours = set(range(ts, week_end, 3600 * 1000))
            if week_hours.issubset(have_ms):
                continue  # already cached
            payload = session.get(_data_url(series_id, region, ts), timeout=30).json()
            rows = [(t, v) for t, v in payload["series"] if v is not None]
            if rows:
                idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
                new_frames.append(pd.Series([r[1] for r in rows], index=idx))
            if pause_s:
                time.sleep(pause_s)

        if new_frames:
            fetched = pd.concat(new_frames)
            fetched.index = fetched.index.tz_convert(tz)
            combined = pd.concat([cached, fetched])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            _save_cache(cache_path, combined)
        else:
            combined = cached
    finally:
        if own_session:
            session.close()

    result = combined[(combined.index >= start_ts) & (combined.index < end_ts)]
    return result.sort_index().rename(f"series_{series_id}")


def build_dataset(
    cfg,
    start: str,
    end: str,
    cache_dir: str | Path = "data/raw",
    pause_s: float = 0.05,
) -> pd.DataFrame:
    """Assemble the modelling dataset: price + day-ahead forecast drivers.

    Columns:
      price                     EUR/MWh, day-ahead wholesale (the target)
      load_forecast             MW, day-ahead load forecast
      solar_forecast            MW
      wind_onshore_forecast     MW
      wind_offshore_forecast    MW
      wind_forecast             MW, on + offshore (derived)
      residual_load_forecast    MW, load - solar - wind (derived; the real
                                price driver -- what thermal plants must cover)

    Every column except the derived ones is a genuine day-ahead forecast,
    published before the 12:00 gate closure, so it is legitimately usable to
    predict the next day's prices with no lookahead.
    """
    ids = cfg.smard_series
    region = cfg.smard_region
    session = requests.Session()
    try:
        cols = {
            "price": ids["price_day_ahead"],
            "load_forecast": ids["load_forecast"],
            "solar_forecast": ids["solar_forecast"],
            "wind_onshore_forecast": ids["wind_onshore_forecast"],
            "wind_offshore_forecast": ids["wind_offshore_forecast"],
        }
        series = {
            name: fetch_series(
                sid, start, end, region=region, cache_dir=cache_dir,
                session=session, tz=cfg.timezone, pause_s=pause_s,
            )
            for name, sid in cols.items()
        }
    finally:
        session.close()

    df = pd.DataFrame(series)
    df["wind_forecast"] = df["wind_onshore_forecast"] + df["wind_offshore_forecast"]
    df["residual_load_forecast"] = (
        df["load_forecast"] - df["solar_forecast"] - df["wind_forecast"]
    )
    # Prices must be complete for the optimizer; forward/back-fill the odd
    # missing forecast hour so feature rows stay usable.
    df = df.dropna(subset=["price"])
    feature_cols = [c for c in df.columns if c != "price"]
    df[feature_cols] = df[feature_cols].ffill().bfill()
    df.index.name = "timestamp"
    return df
