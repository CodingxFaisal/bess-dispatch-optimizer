import datetime as dt

import pandas as pd
import streamlit as st

from gridflex.reporting import figures
from ui_common import bundle, csv_button, require, show

b = bundle()
require(b.market, "market data", "uv run python scripts/run_backtest.py --offline")
assert b.market is not None
tz = b.timezone
market = b.market
local_day = market["interval_utc"].dt.tz_convert(tz).dt.date
lo, hi = local_day.min(), local_day.max()

picked = st.date_input(
    "Delivery days",
    value=(max(lo, hi - dt.timedelta(days=90)), hi),
    min_value=lo,
    max_value=hi,
    key="market_range",
)
if not isinstance(picked, tuple) or len(picked) != 2:
    st.info("Select a start and an end day.")
    st.stop()
start, end = picked
part = market[(local_day >= start) & (local_day <= end)]
if part.empty:
    st.info("No data in the selected range.")
    st.stop()

show(
    figures.price_history(part, tz), tz, "Day-ahead price history with negative prices"
)
c1, c2 = st.columns(2)
with c1:
    show(
        figures.negative_prices_by_month(part, tz), tz, "Negative-price hours per month"
    )
with c2:
    if "load_forecast" in part:
        show(figures.load_forecast(part, tz), tz, "Day-ahead load forecast")
st.info(
    "PV and wind forecasts are published after the 12:00 gate closure and are not "
    "used by any strategy (audit D1); they are not part of the run outputs.",
    icon=":material/visibility_off:",
)

st.subheader("Data-quality diagnostics")
validation = (b.metadata or {}).get("data_provenance", {}).get("validation", {})
rows = []
for key, rep in validation.items():
    issues = rep.get("issues", []) or [
        {"code": "none", "severity": "-", "message": "", "count": 0}
    ]
    for i in issues:
        rows.append(
            {
                "series": key,
                "ok": rep.get("ok"),
                "code": i["code"],
                "severity": i["severity"],
                "count": i["count"],
                "message": i["message"],
            }
        )
diag = pd.DataFrame(rows)
st.dataframe(diag, hide_index=True, alt="Validation report per series")
st.caption(
    "Validation never fills or repairs data; missing or invalid prices abort a "
    "run (docs/DATA_DICTIONARY.md §5)."
)
csv_button(
    part.assign(interval_utc=part["interval_utc"].astype(str)),
    f"market_data_{start}_{end}.csv",
    "Download market data (CSV)",
)
