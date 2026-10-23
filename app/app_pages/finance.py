import streamlit as st

from gridflex.reporting import figures
from ui_common import bundle, csv_button, require, show

b = bundle()
require(b.finance_summary, "financial analysis", "uv run python scripts/run_finance.py")
assert b.finance_summary is not None
tz = b.timezone
fin = b.finance_summary

st.caption(
    "Net operating revenue = gross revenue - degradation - trading cost; not "
    "profit. Annualisation on a 365-day basis (docs/RESULTS.md §3.1)."
)
show(
    figures.revenue_breakdown(fin, tz), tz, "Gross revenue, costs and net per strategy"
)
st.dataframe(
    fin[
        [
            "strategy",
            "days",
            "partial_year",
            "gross_revenue_eur",
            "degradation_cost_eur",
            "trading_cost_eur",
            "net_operating_revenue_eur",
            "annualised_net_eur",
            "net_per_mw_year_eur",
            "net_per_mwh_year_eur",
            "efc_per_day",
            "negative_net_days",
            "capture_ratio",
        ]
    ].round(3),
    hide_index=True,
    alt="Financial summary per strategy",
)

if b.by_month is not None:
    st.subheader("Monthly and annual performance")
    pick = st.pills(
        "Strategies",
        b.strategies,
        selection_mode="multi",
        default=b.strategies[:3],
        key="fin_strats",
    )
    if pick:
        show(
            figures.monthly_net(b.by_month, list(pick), tz),
            tz,
            "Monthly net operating revenue per strategy",
        )
    if b.by_year is not None:
        st.dataframe(
            b.by_year, hide_index=True, alt="Annual totals with completeness flag"
        )

if b.sensitivity is not None:
    st.subheader("Battery duration and sensitivity")
    period = (str(fin["start_day"].min()), str(fin["end_day_inclusive"].max()))
    show(
        figures.duration_comparison(b.sensitivity, period, tz),
        tz,
        "Revenue per MW-year and per MWh-year against duration",
    )
    param = st.segmented_control(
        "Parameter",
        list(figures.SENSITIVITY_UNITS),
        format_func=lambda p: figures.SENSITIVITY_UNITS[p],
        default="degradation_cost_factor",
        key="sens_param",
    )
    if param:
        show(
            figures.sensitivity(b.sensitivity, param, period, tz),
            tz,
            f"Sensitivity of revenue per MW-year to {param}",
        )
    csv_button(b.sensitivity, "sensitivity.csv", "Download sensitivity (CSV)")
else:
    st.info(
        "Run `uv run python scripts/run_sensitivity.py --offline` for sensitivity "
        "results."
    )

st.subheader("Investment metrics")
inv = (b.investment or {}).get("results", {})
if inv:
    first = next(iter(inv.values()))
    if first.get("payback_missing"):
        st.info(
            "Payback and NPV are only computed when every input is configured "
            f"(missing: {', '.join(first['payback_missing'])}).",
            title="Not computed",
            icon=":material/block:",
        )
    else:
        st.json(inv)
csv_button(fin, "finance_summary.csv", "Download financial summary (CSV)")
