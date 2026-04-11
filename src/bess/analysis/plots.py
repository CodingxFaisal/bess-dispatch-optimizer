"""Figures for the README and results folder. Each function saves one chart.

Styling is centralised through seaborn so every chart in the project shares one
palette and grid style. Colours carry meaning: blue for charging and forecasts,
red for discharging, green for state of charge, grey for realised prices.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend, no display required
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

# One consistent look for the whole project (seaborn "deep" palette family).
sns.set_theme(style="whitegrid", context="notebook")

CHARGE = "#4C72B0"     # blue
DISCHARGE = "#C44E52"  # red
SOC = "#55A868"        # green
PRICE = "#4C4C4C"      # dark grey

plt.rcParams.update({
    "figure.dpi": 120,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
})


def _save(fig, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_dispatch_week(
    schedule: pd.DataFrame,
    out_path: str | Path,
    title: str = "Battery dispatch vs. day-ahead price (one week)",
) -> Path:
    """Two panels: price on top, charge/discharge power and SoC below."""
    fig, (ax_p, ax_d) = plt.subplots(
        2, 1, figsize=(11, 6), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.2]},
    )

    ax_p.plot(schedule.index, schedule["price_actual"], color=PRICE,
              lw=1.4, label="realised price")
    if "price_forecast" in schedule:
        ax_p.plot(schedule.index, schedule["price_forecast"], color=CHARGE,
                  lw=1.0, ls="--", alpha=0.85, label="forecast price")
    ax_p.axhline(0, color="k", lw=0.6, alpha=0.5)
    ax_p.set_ylabel("EUR/MWh")
    ax_p.legend(loc="upper right", fontsize=8)
    ax_p.set_title(title)

    ax_d.bar(schedule.index, schedule["p_charge_mw"], width=0.04,
             color=CHARGE, label="charge")
    ax_d.bar(schedule.index, -schedule["p_discharge_mw"], width=0.04,
             color=DISCHARGE, label="discharge")
    ax_d.set_ylabel("Power (MW)")
    ax_d.axhline(0, color="k", lw=0.6, alpha=0.5)

    ax_soc = ax_d.twinx()
    ax_soc.plot(schedule.index, schedule["soc_mwh"], color=SOC, lw=1.8,
                label="state of charge")
    ax_soc.set_ylabel("SoC (MWh)", color=SOC)
    ax_soc.tick_params(axis="y", labelcolor=SOC)
    ax_soc.grid(False)

    handles = (ax_d.get_legend_handles_labels()[0]
               + ax_soc.get_legend_handles_labels()[0])
    labels = (ax_d.get_legend_handles_labels()[1]
              + ax_soc.get_legend_handles_labels()[1])
    ax_d.legend(handles, labels, loc="upper right", fontsize=8, ncol=3)

    ax_d.xaxis.set_major_formatter(mdates.DateFormatter("%a %d", tz=schedule.index.tz))
    fig.autofmt_xdate()
    return _save(fig, out_path)


def plot_capture_ratio(
    net_by_forecaster: dict[str, float],
    perfect_net: float,
    out_path: str | Path,
    title: str = "Foresight capture: share of the ceiling each forecast reaches",
) -> Path:
    """Horizontal bars of capture ratio (net / perfect) per forecaster."""
    order = ["naive", "seasonal_naive", "ml"]
    labels = {"naive": "Naive persistence",
              "seasonal_naive": "Seasonal naive",
              "ml": "Gradient boosting"}
    names = [n for n in order if n in net_by_forecaster]
    ratios = [net_by_forecaster[n] / perfect_net * 100 for n in names]

    fig, ax = plt.subplots(figsize=(9, 3.2))
    colors = ["#B0B8C4", "#7F8CA0", CHARGE]
    bars = ax.barh([labels[n] for n in names], ratios, color=colors[: len(names)])
    ax.axvline(100, color=DISCHARGE, ls="--", lw=1.2, label="perfect foresight (100%)")
    ax.set_xlabel("Share of perfect-foresight revenue (%)")
    ax.set_xlim(0, 110)
    for bar, r in zip(bars, ratios):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{r:.0f}%", va="center", fontsize=10, fontweight="bold")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title(title)
    return _save(fig, out_path)


def plot_revenue_by_year(
    by_year: dict[str, pd.Series],
    out_path: str | Path,
    title: str = "Annual revenue by price regime",
) -> Path:
    """Grouped bars of annual net revenue per forecaster across years."""
    df = pd.DataFrame(by_year)
    fig, ax = plt.subplots(figsize=(9, 4))
    df.plot(kind="bar", ax=ax, color={"perfect": PRICE, "ml": CHARGE,
                                      "seasonal_naive": "#7F8CA0",
                                      "naive": "#B0B8C4"})
    ax.set_ylabel("Net revenue (EUR)")
    ax.set_xlabel("Year")
    ax.set_title(title)
    ax.legend(title="strategy", fontsize=8)
    ax.tick_params(axis="x", rotation=0)
    return _save(fig, out_path)


def plot_duration_sweep(
    sweep: pd.DataFrame,
    out_path: str | Path,
    title: str = "Revenue vs. storage duration",
) -> Path:
    """Twin-axis line chart: revenue per MW-year and per MWh-year vs. duration."""
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(sweep["duration_h"], sweep["revenue_per_mw_year_eur"],
            "o-", color=CHARGE, lw=2, label="EUR / MW-year")
    ax.set_xlabel("Storage duration (hours)")
    ax.set_ylabel("EUR / MW-year", color=CHARGE)
    ax.tick_params(axis="y", labelcolor=CHARGE)

    ax2 = ax.twinx()
    ax2.plot(sweep["duration_h"], sweep["revenue_per_mwh_year_eur"],
             "s--", color=DISCHARGE, lw=2, label="EUR / MWh-year")
    ax2.set_ylabel("EUR / MWh-year", color=DISCHARGE)
    ax2.tick_params(axis="y", labelcolor=DISCHARGE)
    ax2.grid(False)
    ax.set_title(title)
    return _save(fig, out_path)
