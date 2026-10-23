import datetime as dt

import streamlit as st

from gridflex.reporting import figures
from ui_common import bundle, csv_button, require, show

b = bundle()
require(
    b.forecast_holdout,
    "forecast evaluation",
    "uv run python scripts/evaluate_forecasts.py --offline",
)
assert b.forecast_holdout is not None and b.forecasts is not None
tz = b.timezone
hold = b.forecast_holdout
f = b.forecasts
days = sorted(f["delivery_day"].astype(str).unique())
period = (days[0], days[-1])

st.caption(
    f"Hold-out {period[0]} to {period[1]} ({tz}); forecasts issued at D-1 12:00 "
    "from data public by then. Models were selected on a separate period "
    "(docs/BACKTEST_METHODOLOGY.md §1.5)."
)

c1, c2 = st.columns([3, 2])
with c1:
    show(figures.model_comparison(hold, period, tz), tz, "MAE and RMSE per model")
with c2:
    st.subheader("Model comparison")
    st.dataframe(
        hold[["label", "n_days", "mae", "rmse", "bias", "rmae"]].round(3),
        hide_index=True,
        alt="Hold-out forecast accuracy table",
        column_config={
            "mae": "MAE (EUR/MWh)",
            "rmse": "RMSE (EUR/MWh)",
            "bias": "Bias (EUR/MWh)",
            "rmae": "rMAE",
        },
    )
    if b.forecast_by_year is not None:
        st.dataframe(
            b.forecast_by_year[["label", "year", "mae", "rmse", "rmae"]].round(3),
            hide_index=True,
            alt="Hold-out accuracy by year",
        )

models = list(dict.fromkeys(f["model_id"]))
chosen = st.pills(
    "Models", models, selection_mode="multi", default=models[:2], key="fc_models"
)
last = dt.date.fromisoformat(days[-1])
week_start = st.date_input(
    "Week starting",
    value=last - dt.timedelta(days=6),
    min_value=dt.date.fromisoformat(days[0]),
    max_value=last,
    key="fc_week",
)
if chosen:
    show(
        figures.forecast_vs_actual(
            f, list(chosen), week_start, week_start + dt.timedelta(days=6), tz
        ),
        tz,
        "Forecast versus realised price for one week",
    )
    show(
        figures.forecast_error_distribution(f, list(chosen), tz),
        tz,
        "Forecast error histogram",
    )

if b.forecast_dm is not None:
    st.subheader("Diebold-Mariano tests")
    st.caption(
        "One-sided; a small p-value means model B is more accurate than model A."
    )
    st.dataframe(b.forecast_dm.round(4), hide_index=True, alt="Diebold-Mariano tests")
st.info(
    "Probabilistic metrics are not implemented: all models produce point forecasts.",
    icon=":material/info:",
)
csv_button(hold, "forecast_holdout_metrics.csv", "Download metrics (CSV)")
