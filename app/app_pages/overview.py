import pandas as pd
import streamlit as st

from gridflex.reporting.runs import methodology_warnings
from ui_common import bundle, fmt_eur, require

b = bundle()
require(b.daily, "backtest", "uv run python scripts/run_backtest.py --offline")
meta = b.metadata or {}
cfg = meta.get("config", {})
bat = cfg.get("battery", {})
period = meta.get("backtest_period", {})

st.caption(
    f"Backtest period {period.get('start_day')} to {period.get('end_day')} "
    "(exclusive), "
    f"local delivery days ({b.timezone}). Run created {meta.get('created_utc')} UTC; "
    f"commit {str((meta.get('git') or {}).get('commit'))[:7]}"
    f"{' (dirty)' if (meta.get('git') or {}).get('dirty') else ''}; "
    f"config {str(meta.get('config_fingerprint'))[:12]}."
)

for w in methodology_warnings(b):
    st.warning(w, icon=":material/info:")

st.subheader("Realised net operating revenue")
fin = b.finance_summary
src = fin if fin is not None else b.summary
assert src is not None
with st.container(horizontal=True):
    for _, row in src.iterrows():
        cap = row.get("capture_ratio", row.get("share_of_perfect_foresight_net"))
        st.metric(
            row["strategy"],
            fmt_eur(row["net_operating_revenue_eur"]),
            delta=None if pd.isna(cap) else f"{cap:.1%} of perfect foresight",
            delta_color="off",
            border=True,
        )

c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.subheader("Asset configuration")
    eff = bat.get("efficiency", {})
    rows = {
        "Charge / discharge power": f"{bat.get('charge_power_mw')} / "
        f"{bat.get('discharge_power_mw')} MW (AC)",
        "Energy capacity (nameplate)": f"{bat.get('energy_capacity_mwh')} MWh",
        "SoC window": f"{bat.get('soc_min_fraction')}-{bat.get('soc_max_fraction')} "
        "of nameplate",
        "Efficiency": f"{eff.get('mode')}: {eff.get('round_trip', '')} "
        f"{eff.get('charge', '')} {eff.get('discharge', '')}".strip(),
        "Cycle cap": f"{bat.get('max_equivalent_full_cycles_per_day')} EFC/day "
        f"({bat.get('efc_basis')})",
        "Degradation": f"{bat.get('degradation', {}).get('cost_basis')}; inputs "
        f"{bat.get('degradation', {}).get('replacement_cost_eur_per_kwh')} "
        "EUR/kWh, "
        f"{bat.get('degradation', {}).get('cycle_life_cycles')} cycles",
        "Terminal policy": str(bat.get("terminal_soc_policy")),
        "Trading cost": f"{cfg.get('economics', {}).get('trading_cost_eur_per_mwh')} "
        "EUR/MWh",
    }
    st.table(pd.DataFrame({"value": rows}), alt="Battery and cost configuration")

with c2, st.container(border=True):
    st.subheader("Throughput and cycles")
    d = b.daily
    t = d.groupby("strategy", sort=False).agg(
        days=("delivery_day", "count"),
        sold_mwh=("energy_sold_mwh", "sum"),
        bought_mwh=("energy_bought_mwh", "sum"),
        efc=("equivalent_full_cycles", "sum"),
    )
    t["efc_per_day"] = t["efc"] / t["days"]
    st.dataframe(
        t.round(2),
        alt="Throughput (MWh) and equivalent full cycles",
        column_config={
            "sold_mwh": "Sold (MWh)",
            "bought_mwh": "Bought (MWh)",
            "efc": "EFC",
            "efc_per_day": "EFC/day",
        },
    )

c3, c4 = st.columns(2)
with c3, st.container(border=True):
    st.subheader("Solver status")
    s = (
        b.daily.assign(solve_status=b.daily["solve_status"].fillna("n/a (no solver)"))
        .groupby(["strategy", "status", "solve_status"])
        .size()
        .rename("days")
        .reset_index()
    )
    st.dataframe(s, hide_index=True, alt="Day status and solver status counts")
    gap = b.daily["mip_gap"].dropna()
    if len(gap):
        st.caption(
            f"Largest MIP gap {gap.max():.1e}; mean solve time "
            f"{b.daily['solve_runtime_s'].dropna().mean() * 1000:.0f} ms per day."
        )

with c4, st.container(border=True):
    st.subheader("Data freshness")
    series = meta.get("data_provenance", {}).get("series", {})
    fresh = pd.DataFrame(
        [
            {
                "series": k,
                "SMARD id": v.get("smard_id"),
                "unit": v.get("unit"),
                "retrieved (UTC)": v.get("retrieved_to_utc"),
                "latest SMARD file created (UTC)": v.get("source_created_latest_utc"),
                "stale chunks": v.get("n_chunks_stale"),
            }
            for k, v in series.items()
        ]
    )
    st.dataframe(fresh, hide_index=True, alt="Data retrieval times per series")
    st.caption("Data: Bundesnetzagentur | SMARD.de, CC BY 4.0.")
