"""GridFlex: battery dispatch and day-ahead market research (read-only viewer).

    uv run streamlit run app/streamlit_app.py

Shows the outputs of saved runs (default ``runs/latest``; override with the
sidebar field or the ``GRIDFLEX_RUN_DIR`` environment variable). Produce them
with the scripts in ``scripts/`` (see RUNNING.md). No optimisation or
forecasting runs in the UI.
"""

import streamlit as st

from ui_common import DEFAULT_RUN

st.set_page_config(
    page_title="GridFlex", page_icon=":material/battery_charging_full:", layout="wide"
)

if "run_dir" not in st.session_state:
    st.session_state.run_dir = str(DEFAULT_RUN)

with st.sidebar:
    st.text_input(
        "Run directory",
        key="run_dir",
        help="Directory containing backtest/, forecast_evaluation/, sensitivity/",
    )
    st.caption("Read-only viewer of saved runs. Re-run the scripts to refresh.")

page = st.navigation(
    [
        st.Page("app_pages/overview.py", title="Overview", icon=":material/dashboard:"),
        st.Page(
            "app_pages/market_data.py",
            title="Market data",
            icon=":material/show_chart:",
        ),
        st.Page(
            "app_pages/forecasts.py",
            title="Forecast evaluation",
            icon=":material/query_stats:",
        ),
        st.Page("app_pages/dispatch.py", title="Dispatch", icon=":material/bolt:"),
        st.Page(
            "app_pages/finance.py", title="Financial analysis", icon=":material/euro:"
        ),
        st.Page(
            "app_pages/comparison.py",
            title="Experiment comparison",
            icon=":material/compare_arrows:",
        ),
    ],
    position="top",
)
st.title(page.title)
page.run()
