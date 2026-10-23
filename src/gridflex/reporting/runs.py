"""Read saved run directories for reporting and the UI (read-only)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

TIMEZONE = "Europe/Berlin"


def _csv(path: Path, **kwargs: Any) -> pd.DataFrame | None:
    return pd.read_csv(path, **kwargs) if path.exists() else None


def _json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@dataclass
class RunBundle:
    """Everything one run directory contains; missing parts are ``None``."""

    root: Path
    daily: pd.DataFrame | None = None
    intervals: pd.DataFrame | None = None
    summary: pd.DataFrame | None = None
    market: pd.DataFrame | None = None
    metadata: dict[str, Any] | None = None
    finance_summary: pd.DataFrame | None = None
    by_year: pd.DataFrame | None = None
    by_month: pd.DataFrame | None = None
    capture_by_month: pd.DataFrame | None = None
    investment: dict[str, Any] | None = None
    forecast_selection: pd.DataFrame | None = None
    forecast_holdout: pd.DataFrame | None = None
    forecast_by_year: pd.DataFrame | None = None
    forecast_dm: pd.DataFrame | None = None
    forecasts: pd.DataFrame | None = None
    sensitivity: pd.DataFrame | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def strategies(self) -> list[str]:
        if self.daily is None:
            return []
        return list(dict.fromkeys(self.daily["strategy"]))

    @property
    def timezone(self) -> str:
        cfg = (self.metadata or {}).get("config", {})
        return str(cfg.get("market", {}).get("timezone", TIMEZONE))


def _utc(frame: pd.DataFrame | None, *cols: str) -> pd.DataFrame | None:
    if frame is None:
        return None
    for c in cols:
        if c in frame:
            frame[c] = pd.to_datetime(frame[c], utc=True)
    return frame


def load_run(root: str | Path) -> RunBundle:
    """Load a run directory (``runs/latest``); absent outputs are listed."""
    root = Path(root)
    bt, fin, fc = (
        root / "backtest",
        root / "backtest" / "finance",
        root / "forecast_evaluation",
    )
    b = RunBundle(root=root)
    b.daily = _csv(bt / "daily.csv")
    b.intervals = _utc(_csv(bt / "intervals.csv"), "interval_utc")
    b.summary = _csv(bt / "summary.csv")
    b.market = _utc(_csv(bt / "market_data.csv"), "interval_utc")
    b.metadata = _json(bt / "metadata.json")
    b.finance_summary = _csv(fin / "summary.csv")
    b.by_year = _csv(fin / "by_year.csv", dtype={"period": str})
    b.by_month = _csv(fin / "by_month.csv", dtype={"period": str})
    b.capture_by_month = _csv(fin / "capture_by_month.csv", dtype={"period": str})
    b.investment = _json(fin / "investment.json")
    b.forecast_selection = _csv(fc / "selection.csv")
    b.forecast_holdout = _csv(fc / "holdout_metrics.csv")
    b.forecast_by_year = _csv(fc / "holdout_by_year.csv")
    b.forecast_dm = _csv(fc / "dm_tests.csv")
    b.forecasts = _utc(_csv(fc / "holdout_forecasts.csv"), "delivery_interval_utc")
    b.sensitivity = _csv(root / "sensitivity" / "sensitivity.csv")
    names = {
        "backtest": b.daily,
        "market data": b.market,
        "finance": b.finance_summary,
        "forecast evaluation": b.forecast_holdout,
        "sensitivity": b.sensitivity,
    }
    b.missing = [k for k, v in names.items() if v is None]
    return b


def methodology_warnings(bundle: RunBundle) -> list[str]:
    """Plain-language caveats that must accompany any figure from this run."""
    warn = [
        "Day-ahead only, price taker, fixed full-day schedule: not a complete "
        "trading simulation (no intraday, balancing, bid curves or market impact).",
        "Net operating revenue is not profit: capital cost, fixed O&M, grid "
        "charges, taxes and financing are excluded.",
        "Perfect foresight uses realised prices and is an upper benchmark, not a "
        "deployable strategy.",
        "Historical backtest results do not predict future revenue.",
    ]
    meta = bundle.metadata or {}
    if (meta.get("git") or {}).get("dirty"):
        warn.append(
            "Run produced from a working tree with uncommitted changes "
            f"(base commit {meta['git'].get('commit', '?')[:7]})."
        )
    deg = meta.get("config", {}).get("battery", {}).get("degradation", {}) or {}
    if "unsourced" in str(deg.get("source", "")).lower():
        warn.append(
            "Degradation cost is an unsourced placeholder; absolute "
            "revenue figures depend strongly on it (RESULTS §3.3)."
        )
    if bundle.daily is not None and (bundle.daily["status"] != "ok").any():
        warn.append("Some days failed or were rejected and were committed idle.")
    return warn
