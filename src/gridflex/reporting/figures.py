"""Plotly figures for reports and the UI (pure functions, no Streamlit).

Every figure's title states what is shown, the time range and the timezone,
and every value axis names its unit (spec §13). ``label_check`` lets tests and
the UI verify that.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

NEG = "#c0392b"
CHARGE = "#2e86c1"
DISCHARGE = "#e67e22"
ENERGY = "#27ae60"
PRICE = "#34495e"
PLAN = "#8e44ad"


def _range_text(start: object, end: object, tz: str) -> str:
    return f"{start} to {end} ({tz})"


def _title(
    main: str, start: object, end: object, tz: str, note: str = ""
) -> dict[str, str]:
    sub = _range_text(start, end, tz) + (f"; {note}" if note else "")
    return {"text": f"{main}<br><sup>{sub}</sup>"}


def _local(ts: pd.Series, tz: str) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(tz)


def _days_range(days: Iterable[object]) -> tuple[str, str]:
    d = sorted(str(x) for x in days)
    return d[0], d[-1]


def label_check(fig: go.Figure, tz: str) -> list[str]:
    """Problems with a figure's labelling (empty list = compliant)."""
    problems = []
    title = fig.layout.title.text or ""
    if tz not in title and "timezone-free" not in title:
        problems.append("title lacks timezone")
    if " to " not in title:
        problems.append("title lacks time range")
    for name in ("yaxis", "yaxis2", "yaxis3"):
        axis = getattr(fig.layout, name, None)
        if (
            axis is not None
            and axis.title.text is not None
            and "(" not in axis.title.text
        ):
            problems.append(f"{name} title lacks unit")
    if fig.layout.yaxis.title.text is None:
        problems.append("yaxis has no title")
    return problems


# --------------------------------------------------------------------------- #
# Market data                                                                 #
# --------------------------------------------------------------------------- #
def price_history(market: pd.DataFrame, tz: str) -> go.Figure:
    local = _local(market["interval_utc"], tz)
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=local,
            y=market["price"],
            mode="lines",
            name="day-ahead price",
            line={"width": 1, "color": PRICE},
        )
    )
    neg = market["price"] < 0
    fig.add_trace(
        go.Scattergl(
            x=local[neg],
            y=market.loc[neg, "price"],
            mode="markers",
            name="negative price",
            marker={"size": 4, "color": NEG},
        )
    )
    fig.update_layout(
        title=_title(
            "DE-LU day-ahead price",
            local.min().date(),
            local.max().date(),
            tz,
            f"{int(neg.sum())} negative-price intervals",
        ),
        yaxis_title="Price (EUR/MWh)",
        xaxis_title=f"Delivery interval start ({tz})",
    )
    return fig


def negative_prices_by_month(market: pd.DataFrame, tz: str) -> go.Figure:
    local = _local(market["interval_utc"], tz)
    month = local.dt.strftime("%Y-%m")
    counts = (market["price"] < 0).groupby(month).sum()
    step_h = market["interval_utc"].diff().median() / pd.Timedelta(hours=1)
    fig = go.Figure(
        go.Bar(
            x=counts.index,
            y=counts.to_numpy() * step_h,
            marker_color=NEG,
            name="hours with price < 0",
        )
    )
    fig.update_layout(
        title=_title(
            "Hours with negative day-ahead price, per month",
            local.min().date(),
            local.max().date(),
            tz,
        ),
        yaxis_title="Negative-price time (h)",
        xaxis_title=f"Month ({tz})",
    )
    return fig


def load_forecast(market: pd.DataFrame, tz: str) -> go.Figure:
    local = _local(market["interval_utc"], tz)
    fig = go.Figure(
        go.Scattergl(
            x=local,
            y=market["load_forecast"],
            mode="lines",
            line={"width": 1},
            name="load forecast",
        )
    )
    fig.update_layout(
        title=_title(
            "Day-ahead total load forecast (SMARD 411)",
            local.min().date(),
            local.max().date(),
            tz,
            "published D-1 10:00",
        ),
        yaxis_title="Mean power (MW)",
        xaxis_title=f"Delivery interval start ({tz})",
    )
    return fig


# --------------------------------------------------------------------------- #
# Forecasts                                                                   #
# --------------------------------------------------------------------------- #
def forecast_vs_actual(
    forecasts: pd.DataFrame, models: list[str], start: dt.date, end: dt.date, tz: str
) -> go.Figure:
    f = forecasts.copy()
    f["local"] = _local(f["delivery_interval_utc"], tz)
    days = pd.to_datetime(f["delivery_day"]).dt.date
    f = f[(days >= start) & (days <= end)]
    fig = go.Figure()
    first = f[f["model_id"] == f["model_id"].iloc[0]] if len(f) else f
    fig.add_trace(
        go.Scatter(
            x=first["local"],
            y=first["price_actual_eur_per_mwh"],
            name="realised",
            line={"color": PRICE, "width": 2},
        )
    )
    for m in models:
        part = f[f["model_id"] == m]
        fig.add_trace(
            go.Scatter(
                x=part["local"],
                y=part["price_forecast_eur_per_mwh"],
                name=f"forecast: {m}",
                line={"width": 1},
            )
        )
    fig.update_layout(
        title=_title(
            "Day-ahead price: forecast vs realised",
            start,
            end,
            tz,
            "forecasts issued D-1 12:00",
        ),
        yaxis_title="Price (EUR/MWh)",
        xaxis_title=f"Delivery interval start ({tz})",
    )
    return fig


def forecast_error_distribution(
    forecasts: pd.DataFrame, models: list[str], tz: str
) -> go.Figure:
    lo, hi = _days_range(forecasts["delivery_day"])
    fig = go.Figure()
    for m in models:
        e = forecasts.loc[forecasts["model_id"] == m, "error_eur_per_mwh"]
        fig.add_trace(
            go.Histogram(x=e.clip(-200, 200), name=m, opacity=0.6, nbinsx=120)
        )
    fig.update_layout(
        barmode="overlay",
        title=_title(
            "Forecast error distribution (forecast - realised)",
            lo,
            hi,
            tz,
            "clipped to +/-200 for display",
        ),
        xaxis_title="Error (EUR/MWh)",
        yaxis_title="Intervals (count)",
    )
    return fig


def model_comparison(
    holdout: pd.DataFrame, period: tuple[str, str], tz: str
) -> go.Figure:
    fig = go.Figure()
    for metric in ("mae", "rmse"):
        fig.add_trace(
            go.Bar(
                x=holdout["label"],
                y=holdout[metric],
                name=metric.upper(),
                text=holdout[metric].round(2),
                textposition="outside",
            )
        )
    fig.update_layout(
        barmode="group",
        title=_title(
            "Hold-out forecast accuracy",
            period[0],
            period[1],
            tz,
            "all delivery intervals",
        ),
        yaxis_title="Error (EUR/MWh)",
        xaxis_title="Model",
    )
    return fig


# --------------------------------------------------------------------------- #
# Dispatch                                                                    #
# --------------------------------------------------------------------------- #
def dispatch_day(
    intervals: pd.DataFrame, strategy: str, days: list[str], tz: str
) -> go.Figure:
    part = intervals[
        (intervals["strategy"] == strategy)
        & (intervals["delivery_day"].astype(str).isin(days))
    ].copy()
    part["local"] = _local(part["interval_utc"], tz)
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.38, 0.32, 0.30],
    )
    fig.add_trace(
        go.Scatter(
            x=part["local"],
            y=part["price_realised_eur_per_mwh"],
            name="realised price",
            line={"color": PRICE, "shape": "hv"},
        ),
        row=1,
        col=1,
    )
    if part["price_plan_eur_per_mwh"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=part["local"],
                y=part["price_plan_eur_per_mwh"],
                name="plan price",
                line={"color": PLAN, "dash": "dot", "shape": "hv"},
            ),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Bar(
            x=part["local"],
            y=part["discharge_mw"],
            name="discharge",
            marker_color=DISCHARGE,
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=part["local"], y=-part["charge_mw"], name="charge", marker_color=CHARGE
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=part["local"],
            y=part["energy_end_mwh"],
            name="stored energy (end of interval)",
            line={"color": ENERGY},
        ),
        row=3,
        col=1,
    )
    fig.update_layout(
        title=_title(
            f"Committed dispatch: {strategy}",
            days[0],
            days[-1],
            tz,
            "AC power at grid connection; charge shown negative",
        ),
        barmode="relative",
        height=640,
    )
    fig.update_yaxes(title_text="Price (EUR/MWh)", row=1, col=1)
    fig.update_yaxes(title_text="Power (MW)", row=2, col=1)
    fig.update_yaxes(title_text="Energy (MWh)", row=3, col=1)
    fig.update_xaxes(title_text=f"Delivery interval start ({tz})", row=3, col=1)
    return fig


def cumulative_net(daily: pd.DataFrame, tz: str) -> go.Figure:
    lo, hi = _days_range(daily["delivery_day"])
    fig = go.Figure()
    for name, d in daily.groupby("strategy", sort=False):
        d = d.sort_values("delivery_day")
        fig.add_trace(
            go.Scatter(
                x=d["delivery_day"],
                y=d["net_operating_revenue_eur"].cumsum(),
                name=str(name),
                mode="lines",
            )
        )
    fig.update_layout(
        title=_title(
            "Cumulative net operating revenue", lo, hi, tz, "local delivery days"
        ),
        yaxis_title="Net operating revenue (EUR)",
        xaxis_title=f"Delivery day ({tz})",
    )
    return fig


# --------------------------------------------------------------------------- #
# Finance                                                                     #
# --------------------------------------------------------------------------- #
def revenue_breakdown(fin: pd.DataFrame, tz: str) -> go.Figure:
    lo, hi = str(fin["start_day"].min()), str(fin["end_day_inclusive"].max())
    fig = go.Figure()
    fig.add_trace(
        go.Bar(x=fin["strategy"], y=fin["gross_revenue_eur"], name="gross revenue")
    )
    fig.add_trace(
        go.Bar(
            x=fin["strategy"], y=-fin["degradation_cost_eur"], name="degradation cost"
        )
    )
    fig.add_trace(
        go.Bar(x=fin["strategy"], y=-fin["trading_cost_eur"], name="trading cost")
    )
    fig.add_trace(
        go.Scatter(
            x=fin["strategy"],
            y=fin["net_operating_revenue_eur"],
            mode="markers+text",
            name="net operating revenue",
            text=(fin["net_operating_revenue_eur"] / 1000)
            .round(0)
            .astype(int)
            .astype(str)
            + "k",
            textposition="top center",
            marker={"size": 12, "color": "black"},
        )
    )
    fig.update_layout(
        barmode="relative",
        title=_title(
            "Revenue and cost breakdown", lo, hi, tz, "totals, not annualised"
        ),
        yaxis_title="Amount (EUR)",
        xaxis_title="Strategy",
    )
    return fig


def monthly_net(by_month: pd.DataFrame, strategies: list[str], tz: str) -> go.Figure:
    m = by_month[by_month["strategy"].isin(strategies)]
    lo, hi = m["period"].min(), m["period"].max()
    fig = go.Figure()
    for s in strategies:
        part = m[m["strategy"] == s]
        fig.add_trace(
            go.Bar(
                x=part["period"],
                y=part["net_operating_revenue_eur"],
                name=s,
                marker_pattern_shape=["/" if not c else "" for c in part["complete"]],
            )
        )
    fig.update_layout(
        barmode="group",
        title=_title(
            "Monthly net operating revenue", lo, hi, tz, "hatched = incomplete month"
        ),
        yaxis_title="Net operating revenue (EUR)",
        xaxis_title=f"Month ({tz})",
    )
    return fig


def duration_comparison(
    sens: pd.DataFrame, period: tuple[str, str], tz: str
) -> go.Figure:
    d = sens[sens["parameter"] == "duration_h"].sort_values("value")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for s, part in d.groupby("strategy", sort=False):
        fig.add_trace(
            go.Scatter(
                x=part["value"],
                y=part["net_per_mw_year_eur"],
                name=f"{s}: EUR/MW-yr",
                mode="lines+markers",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=part["value"],
                y=part["net_per_mwh_year_eur"],
                name=f"{s}: EUR/MWh-yr",
                mode="lines+markers",
                line={"dash": "dash"},
            ),
            secondary_y=True,
        )
    fig.update_layout(
        title=_title(
            "Battery duration comparison (power fixed)",
            period[0],
            period[1],
            tz,
            "annualised, 365-day basis",
        ),
        xaxis_title="Duration (h)",
    )
    fig.update_yaxes(title_text="Net per MW-year (EUR/MW-yr)", secondary_y=False)
    fig.update_yaxes(title_text="Net per MWh-year (EUR/MWh-yr)", secondary_y=True)
    return fig


SENSITIVITY_UNITS = {
    "power_mw": "Power (MW), energy fixed",
    "duration_h": "Duration (h), power fixed",
    "round_trip_efficiency": "Round-trip efficiency (fraction)",
    "degradation_cost_factor": "Degradation cost (x base)",
    "max_efc_per_day": "Cycle cap (EFC/day)",
}


def sensitivity(
    sens: pd.DataFrame, parameter: str, period: tuple[str, str], tz: str
) -> go.Figure:
    d = sens[sens["parameter"] == parameter].copy()
    cap_none = d["value"].isna()
    d["x"] = d["value"].astype(object).where(~cap_none, "none").astype(str)
    fig = go.Figure()
    for s, part in d.groupby("strategy", sort=False):
        fig.add_trace(
            go.Scatter(
                x=part["x"],
                y=part["net_per_mw_year_eur"],
                name=str(s),
                mode="lines+markers",
            )
        )
    fig.update_layout(
        title=_title(
            f"Sensitivity: {SENSITIVITY_UNITS.get(parameter, parameter)}",
            period[0],
            period[1],
            tz,
            "one at a time; others at base",
        ),
        yaxis_title="Net per MW-year (EUR/MW-yr)",
        xaxis_title=SENSITIVITY_UNITS.get(parameter, parameter),
        xaxis_type="category",
    )
    return fig


def strategy_comparison(fin: pd.DataFrame, tz: str) -> go.Figure:
    lo, hi = str(fin["start_day"].min()), str(fin["end_day_inclusive"].max())
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=fin["strategy"], y=fin["net_per_mw_year_eur"], name="net per MW-year")
    )
    fig.add_trace(
        go.Scatter(
            x=fin["strategy"],
            y=fin["capture_ratio"],
            name="capture ratio",
            mode="markers",
            marker={"size": 12},
        ),
        secondary_y=True,
    )
    fig.update_layout(
        title=_title(
            "Strategy comparison (identical assumptions)",
            lo,
            hi,
            tz,
            "annualised, 365-day basis",
        )
    )
    fig.update_yaxes(title_text="Net per MW-year (EUR/MW-yr)", secondary_y=False)
    fig.update_yaxes(
        title_text="Capture ratio (fraction of perfect foresight)",
        secondary_y=True,
        range=[0, 1.05],
    )
    return fig
