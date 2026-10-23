"""Financial metrics of a backtest, with explicit formulas.

Inputs are the per-day rows produced by :func:`gridflex.backtest.run_strategy`
(``BacktestResult.daily`` or ``runs/.../daily.csv``). All money is EUR,
energy MWh, power MW. Formulas (docs/RESULTS.md, "Metric definitions"):

* ``net`` = sum of daily net operating revenue = gross − degradation − trading.
* Annualisation, **365-day basis**: ``annualised_X = X_total * 365 / n_days``.
  The period is flagged ``partial_year`` when ``n_days < 365``; annualised
  figures then extrapolate and are labelled as such.
* ``net_per_mw_year`` = annualised net / rated discharge power.
* ``net_per_mwh_year`` = annualised net / nameplate energy capacity.
* ``efc_per_day`` = total equivalent full cycles / n_days (configured basis).
* **Forecast capture ratio** = net(strategy) / net(perfect foresight), summed
  over the *same delivery days* with the *same battery, costs and start state*.
  It is undefined (NaN) when the benchmark net is not positive.

Net operating revenue is not profit: capital cost, fixed O&M, grid charges,
taxes, financing and imbalance risk are excluded.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from gridflex.config.models import BatteryConfig

ANNUALISATION = "365-day basis: annualised = total * 365 / days"
_SUMS = {
    "gross_revenue_eur": "gross_revenue_eur",
    "degradation_cost_eur": "degradation_cost_eur",
    "trading_cost_eur": "trading_cost_eur",
    "net_operating_revenue_eur": "net_operating_revenue_eur",
    "energy_bought_mwh": "energy_bought_mwh",
    "energy_sold_mwh": "energy_sold_mwh",
    "equivalent_full_cycles": "equivalent_full_cycles",
}


def _days(daily: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(daily["delivery_day"]).dt.date


@dataclass(frozen=True)
class FinancialSummary:
    strategy: str
    start_day: dt.date
    end_day_inclusive: dt.date
    days: int
    failed_days: int
    partial_year: bool
    annualisation: str
    gross_revenue_eur: float
    degradation_cost_eur: float
    trading_cost_eur: float
    net_operating_revenue_eur: float
    energy_bought_mwh: float
    energy_sold_mwh: float
    equivalent_full_cycles: float
    efc_per_day: float
    mean_daily_net_eur: float
    std_daily_net_eur: float
    negative_net_days: int
    annualised_net_eur: float
    annualised_degradation_eur: float
    net_per_mw_year_eur: float
    net_per_mwh_year_eur: float
    capture_ratio: float | None = None
    gross_capture_ratio: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarise(
    daily: pd.DataFrame,
    battery: BatteryConfig,
    *,
    benchmark: pd.DataFrame | None = None,
) -> FinancialSummary:
    """Summarise one strategy's daily rows (optionally against a benchmark)."""
    if daily.empty:
        raise ValueError("no daily rows to summarise")
    days = _days(daily)
    if days.duplicated().any():
        raise ValueError("daily rows must have one row per delivery day")
    n = len(daily)
    tot = {k: float(daily[c].sum()) for k, c in _SUMS.items()}
    factor = 365.0 / n
    net = tot["net_operating_revenue_eur"]
    capture = gross_capture = None
    if benchmark is not None:
        bdays = _days(benchmark)
        if set(bdays) != set(days):
            raise ValueError("benchmark must cover exactly the same delivery days")
        b_net = float(benchmark["net_operating_revenue_eur"].sum())
        b_gross = float(benchmark["gross_revenue_eur"].sum())
        capture = net / b_net if b_net > 0 else math.nan
        gross_capture = tot["gross_revenue_eur"] / b_gross if b_gross > 0 else math.nan
    daily_net = daily["net_operating_revenue_eur"].to_numpy(dtype=np.float64)
    return FinancialSummary(
        strategy=str(daily["strategy"].iloc[0]) if "strategy" in daily else "",
        start_day=min(days),
        end_day_inclusive=max(days),
        days=n,
        failed_days=int((daily["status"] != "ok").sum()) if "status" in daily else 0,
        partial_year=n < 365,
        annualisation=ANNUALISATION,
        **tot,
        efc_per_day=tot["equivalent_full_cycles"] / n,
        mean_daily_net_eur=float(daily_net.mean()),
        std_daily_net_eur=float(daily_net.std(ddof=1)) if n > 1 else 0.0,
        negative_net_days=int((daily_net < 0).sum()),
        annualised_net_eur=net * factor,
        annualised_degradation_eur=tot["degradation_cost_eur"] * factor,
        net_per_mw_year_eur=net * factor / battery.discharge_power_mw,
        net_per_mwh_year_eur=net * factor / battery.energy_capacity_mwh,
        capture_ratio=capture,
        gross_capture_ratio=gross_capture,
    )


def period_table(
    daily: pd.DataFrame, battery: BatteryConfig, freq: Literal["month", "year"]
) -> pd.DataFrame:
    """Totals per calendar month or year; incomplete periods flagged.

    ``complete`` is false when the period has fewer backtest days than
    calendar days (e.g. a partial first or last month). ``net_per_mw_eur`` is
    the period total per MW of discharge power (not annualised).
    """
    days = pd.to_datetime(daily["delivery_day"])
    key = days.dt.strftime("%Y-%m" if freq == "month" else "%Y")
    grouped = daily.assign(period=key).groupby("period", sort=True)
    out = grouped[list(_SUMS)].sum()
    out.insert(0, "days", grouped.size())

    def calendar_days(p: str) -> int:
        if freq == "month":
            y, m = (int(x) for x in p.split("-"))
            return calendar.monthrange(y, m)[1]
        return 366 if calendar.isleap(int(p)) else 365

    out.insert(1, "calendar_days", [calendar_days(p) for p in out.index])
    out.insert(2, "complete", out["days"] == out["calendar_days"])
    out["net_per_mw_eur"] = (
        out["net_operating_revenue_eur"] / battery.discharge_power_mw
    )
    out["negative_net_days"] = grouped["net_operating_revenue_eur"].apply(
        lambda s: int((s < 0).sum())
    )
    return out.reset_index()


def capture_by_period(
    daily: pd.DataFrame, benchmark: pd.DataFrame, freq: Literal["month", "year"]
) -> pd.DataFrame:
    """Capture ratio per period (same definition as :func:`summarise`)."""

    def totals(frame: pd.DataFrame) -> pd.Series:
        key = pd.to_datetime(frame["delivery_day"]).dt.strftime(
            "%Y-%m" if freq == "month" else "%Y"
        )
        return frame.groupby(key)["net_operating_revenue_eur"].sum()

    s, b = totals(daily), totals(benchmark)
    if not s.index.equals(b.index):
        raise ValueError("benchmark must cover the same periods")
    ratio = (s / b).where(b > 0)
    return pd.DataFrame(
        {
            "period": s.index,
            "net_eur": s.to_numpy(),
            "benchmark_net_eur": b.to_numpy(),
            "capture_ratio": ratio.to_numpy(),
        }
    )
