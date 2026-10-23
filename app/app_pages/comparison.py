import streamlit as st

from gridflex.reporting import figures
from ui_common import bundle, config_yaml, csv_button, metadata_json, require, show

b = bundle()
require(b.daily, "backtest", "uv run python scripts/run_backtest.py --offline")
assert b.daily is not None
tz = b.timezone
meta = b.metadata or {}

st.caption(
    "All strategies use the same delivery days, battery, costs, policy and start "
    "state; only the information and planning method differ."
)
if b.finance_summary is not None:
    show(
        figures.strategy_comparison(b.finance_summary, tz),
        tz,
        "Net per MW-year and capture ratio per strategy",
    )
show(figures.cumulative_net(b.daily, tz), tz, "Cumulative net operating revenue")

strategies = [s.get("name") for s in meta.get("strategies", [])]
desc = [
    {
        "strategy": s.get("name"),
        "kind": s.get("kind"),
        "uses future information": s.get("uses_future_information"),
        "model": (s.get("model") or {}).get("model", "-"),
    }
    for s in meta.get("strategies", [])
]
st.dataframe(desc, hide_index=True, alt="Strategy definitions")

st.subheader("Exports")
with st.container(horizontal=True):
    csv_button(b.daily, "daily.csv", "Daily results (CSV)")
    if b.summary is not None:
        csv_button(b.summary, "summary.csv", "Summary (CSV)")
    if b.finance_summary is not None:
        csv_button(b.finance_summary, "finance_summary.csv", "Financial summary (CSV)")
    if b.intervals is not None:
        csv_button(
            b.intervals.assign(interval_utc=b.intervals["interval_utc"].astype(str)),
            "intervals.csv",
            "Interval schedules (CSV)",
        )
    st.download_button(
        "Configuration (YAML)",
        config_yaml(meta),
        file_name="config.yaml",
        mime="application/x-yaml",
        icon=":material/settings:",
    )
    st.download_button(
        "Run metadata (JSON)",
        metadata_json(meta),
        file_name="metadata.json",
        mime="application/json",
        icon=":material/description:",
    )
st.caption(
    f"Config fingerprint {meta.get('config_fingerprint')}; strategies "
    f"{', '.join(str(s) for s in strategies)}. Data: Bundesnetzagentur | SMARD.de, "
    "CC BY 4.0."
)
