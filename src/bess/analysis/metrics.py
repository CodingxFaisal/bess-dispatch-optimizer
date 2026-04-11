"""Reporting metrics: annualised revenue, capture ratio, payback, regime splits.

These are the numbers a BESS asset manager actually reads. The single most
important one is the *foresight capture ratio*: realistic revenue divided by
perfect-foresight revenue. Perfect-foresight revenue is fiction (nobody knows
tomorrow's prices); the capture ratio says how much of that ceiling a real,
forecast-driven operation reaches.
"""

from __future__ import annotations

import pandas as pd

from bess.backtest.rolling import BacktestResult
from bess.config import BatteryConfig

_HOURS_PER_YEAR = 8760.0


def annualize(net_eur: float, n_days: int) -> float:
    """Scale a partial-period revenue to an annual figure (365-day basis)."""
    if n_days <= 0:
        return 0.0
    return net_eur * 365.0 / n_days


def capture_ratio(realistic_net: float, perfect_net: float) -> float:
    """Realistic net revenue as a fraction of the perfect-foresight ceiling."""
    if perfect_net == 0:
        return float("nan")
    return realistic_net / perfect_net


def revenue_by_year(result: BacktestResult) -> pd.Series:
    """Net settled revenue grouped by calendar year (the price-regime split)."""
    daily = result.daily
    by_year = daily.groupby(daily.index.year)["net_settled_eur"].sum()
    by_year.index.name = "year"
    return by_year.rename("net_settled_eur")


def payback_years(cfg: BatteryConfig, annual_net_eur: float) -> float:
    """Simple (undiscounted) payback given the configured capex."""
    capex_total = cfg.capex_eur_per_kwh * cfg.energy_mwh * 1000.0  # EUR/kWh * kWh
    if annual_net_eur <= 0:
        return float("inf")
    return capex_total / annual_net_eur


def summarize_backtest(
    result: BacktestResult,
    cfg: BatteryConfig,
    perfect_net_eur: float | None = None,
) -> dict:
    """One-stop summary dict for a single backtest run.

    If ``perfect_net_eur`` is given, the capture ratio is included.
    """
    n_days = result.n_days
    net = result.net_revenue_eur
    annual_net = annualize(net, n_days)

    summary = {
        "forecaster": result.forecaster_name,
        "days": n_days,
        "net_revenue_eur": net,
        "planned_revenue_eur": result.planned_revenue_eur,
        "annual_net_eur": annual_net,
        "revenue_per_mw_year_eur": annual_net / cfg.power_mw,
        "revenue_per_mwh_year_eur": annual_net / cfg.energy_mwh,
        "equivalent_full_cycles_per_year": annualize(
            result.equivalent_full_cycles, n_days
        ),
        "payback_years": payback_years(cfg, annual_net),
    }
    if perfect_net_eur is not None:
        summary["capture_ratio"] = capture_ratio(net, perfect_net_eur)
    return summary


def summary_table(summaries: list[dict]) -> pd.DataFrame:
    """Stack per-forecaster summaries into a tidy comparison table."""
    df = pd.DataFrame(summaries).set_index("forecaster")
    return df
