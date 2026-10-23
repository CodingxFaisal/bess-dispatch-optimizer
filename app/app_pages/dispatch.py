import datetime as dt

import pandas as pd
import streamlit as st

from gridflex.reporting import figures
from ui_common import bundle, csv_button, require, show

b = bundle()
require(
    b.intervals, "dispatch schedules", "uv run python scripts/run_backtest.py --offline"
)
assert b.intervals is not None and b.daily is not None
tz = b.timezone
days = sorted(b.daily["delivery_day"].astype(str).unique())
first, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])

with st.container(horizontal=True, vertical_alignment="bottom"):
    strategy = st.selectbox("Strategy", b.strategies, key="disp_strategy")
    day = st.date_input(
        "First delivery day",
        value=last - dt.timedelta(days=2),
        min_value=first,
        max_value=last,
        key="disp_day",
    )
    span = st.segmented_control("Days shown", [1, 3, 7], default=3, key="disp_span")
span = span or 1
shown = [
    str(day + dt.timedelta(days=i))
    for i in range(span)
    if day + dt.timedelta(days=i) <= last
]

show(
    figures.dispatch_day(b.intervals, strategy, shown, tz),
    tz,
    "Price, charge and discharge power, and stored energy",
)

st.subheader("Solver status and constraint diagnostics")
rows = b.daily[
    (b.daily["strategy"] == strategy) & b.daily["delivery_day"].astype(str).isin(shown)
]
cols = [
    "delivery_day",
    "status",
    "reason",
    "n_intervals",
    "e_start_mwh",
    "e_end_mwh",
    "equivalent_full_cycles",
    "solve_status",
    "mip_gap",
    "solve_runtime_s",
    "expected_net_eur",
    "net_operating_revenue_eur",
    "forecast_mae_eur_per_mwh",
]
st.dataframe(
    rows[cols].round(4),
    hide_index=True,
    alt="Per-day status, energy and solver diagnostics",
)

cfg = (b.metadata or {}).get("config", {}).get("battery", {})
allrows = b.daily[b.daily["strategy"] == strategy]
cap = cfg.get("max_equivalent_full_cycles_per_day")
checks = {
    "days verified and settled (status ok)": f"{(allrows['status'] == 'ok').sum()} of "
    f"{len(allrows)}",
    "largest daily EFC": f"{allrows['equivalent_full_cycles'].max():.4f} "
    f"(cap {cap} per 24 h, pro-rated for 23/25 h days)",
    "start-energy range carried": f"{allrows['e_start_mwh'].min():.6f} to "
    f"{allrows['e_start_mwh'].max():.6f} MWh",
}
st.table(
    pd.DataFrame({"value": checks}),
    alt="Constraint diagnostics over the whole backtest",
)
st.caption(
    "Every committed schedule passed the independent physics checker "
    "(power, simultaneity, SoC bounds, terminal policy, cycle cap) before "
    "settlement; failures would show status 'failed' or 'rejected'."
)
part = b.intervals[
    (b.intervals["strategy"] == strategy)
    & b.intervals["delivery_day"].astype(str).isin(shown)
]
csv_button(
    part.assign(interval_utc=part["interval_utc"].astype(str)),
    f"dispatch_{strategy}_{shown[0]}.csv",
    "Download shown schedule (CSV)",
)
