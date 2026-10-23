"""Shared UI helpers. The UI only reads saved runs; no modelling happens here."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from gridflex.reporting.figures import label_check
from gridflex.reporting.runs import RunBundle, load_run

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = Path(os.environ.get("GRIDFLEX_RUN_DIR", REPO_ROOT / "runs" / "latest"))
ATTRIBUTION = "Data: Bundesnetzagentur | SMARD.de, CC BY 4.0."


def _mtime(root: Path) -> float:
    files = list(root.rglob("*.csv")) + list(root.rglob("*.json"))
    return max((f.stat().st_mtime for f in files), default=0.0)


@st.cache_data(ttl=600, max_entries=4, show_spinner="Loading run outputs...")
def _load(root: str, mtime: float) -> RunBundle:
    return load_run(root)


def bundle() -> RunBundle:
    root = Path(st.session_state.get("run_dir", str(DEFAULT_RUN)))
    return _load(str(root), _mtime(root))


def require(value: Any, what: str, command: str) -> None:
    """Stop the page with guidance when an output is missing."""
    if value is None:
        st.info(
            f"Run `{command}` to produce this output.", title=f"No {what} in this run"
        )
        st.stop()


def show(fig: go.Figure, tz: str, alt: str, key: str | None = None) -> None:
    problems = label_check(fig, tz)
    if problems:  # defensive: every figure must carry units, range and timezone
        st.error("; ".join(problems), title="Figure labelling problem")
    st.plotly_chart(fig, alt=alt, key=key)
    st.caption(ATTRIBUTION)


def csv_button(df: pd.DataFrame, filename: str, label: str) -> None:
    st.download_button(
        label,
        df.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        icon=":material/download:",
    )


def config_yaml(meta: dict[str, Any]) -> str:
    return yaml.safe_dump(meta.get("config", {}), sort_keys=False, allow_unicode=True)


def metadata_json(meta: dict[str, Any]) -> str:
    buf = io.StringIO()
    json.dump(meta, buf, indent=2, default=str)
    return buf.getvalue()


def fmt_eur(x: float) -> str:
    return f"{x:,.0f} EUR"
