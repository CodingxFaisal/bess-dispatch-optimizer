"""Run loading, export guard, figure labelling and the read-only UI."""

import ast
import json
from pathlib import Path

import plotly.graph_objects as go
import pytest
from streamlit.testing.v1 import AppTest

from gridflex.config import GridFlexConfig
from gridflex.reporting import figures
from gridflex.reporting.export import FingerprintMismatchError, write_finance
from gridflex.reporting.runs import load_run, methodology_warnings
from tests_helpers import REPO_ROOT

TZ = "Europe/Berlin"
APP = str(REPO_ROOT / "app" / "streamlit_app.py")
PAGES = ["overview", "market_data", "forecasts", "dispatch", "finance", "comparison"]


def test_load_run_complete(mini_run: Path) -> None:
    b = load_run(mini_run)
    assert b.missing == []
    assert b.strategies == [
        "perfect_foresight",
        "forecast_persistence",
        "heuristic_persistence",
    ]
    assert str(b.intervals["interval_utc"].dt.tz) == "UTC"  # type: ignore[index]
    assert b.timezone == TZ
    assert len(b.market) == 335  # type: ignore[arg-type]  # 14 days * 24 - 1 (23 h day)


def test_load_run_reports_missing_parts(tmp_path: Path) -> None:
    b = load_run(tmp_path)
    assert set(b.missing) == {
        "backtest",
        "market data",
        "finance",
        "forecast evaluation",
        "sensitivity",
    }
    assert b.strategies == []


def test_methodology_warnings(mini_run: Path) -> None:
    w = " ".join(methodology_warnings(load_run(mini_run)))
    assert "not profit" in w and "upper benchmark" in w and "unsourced" in w


def test_finance_refuses_other_config(mini_run: Path, gf_cfg: GridFlexConfig) -> None:
    other = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(update={"energy_capacity_mwh": 40.0})
        }
    )
    with pytest.raises(FingerprintMismatchError):
        write_finance(mini_run / "backtest", other)


def _all_figures(mini_run: Path) -> dict[str, go.Figure]:
    b = load_run(mini_run)
    assert b.market is not None and b.forecasts is not None and b.intervals is not None
    assert b.finance_summary is not None and b.by_month is not None
    assert b.sensitivity is not None and b.daily is not None
    import datetime as dt

    return {
        "price": figures.price_history(b.market, TZ),
        "negative": figures.negative_prices_by_month(b.market, TZ),
        "load": figures.load_forecast(b.market, TZ),
        "fc": figures.forecast_vs_actual(
            b.forecasts, ["persistence"], dt.date(2024, 4, 1), dt.date(2024, 4, 7), TZ
        ),
        "err": figures.forecast_error_distribution(b.forecasts, ["persistence"], TZ),
        "models": figures.model_comparison(
            b.forecast_holdout, ("2024-04-01", "2024-04-07"), TZ
        ),
        "dispatch": figures.dispatch_day(
            b.intervals, "perfect_foresight", ["2024-03-31", "2024-04-01"], TZ
        ),
        "cum": figures.cumulative_net(b.daily, TZ),
        "breakdown": figures.revenue_breakdown(b.finance_summary, TZ),
        "monthly": figures.monthly_net(b.by_month, b.strategies, TZ),
        "duration": figures.duration_comparison(b.sensitivity, ("a", "b"), TZ),
        **{
            f"sens_{p}": figures.sensitivity(b.sensitivity, p, ("a", "b"), TZ)
            for p in figures.SENSITIVITY_UNITS
        },
        "compare": figures.strategy_comparison(b.finance_summary, TZ),
    }


def test_every_figure_shows_units_range_and_timezone(mini_run: Path) -> None:
    for name, fig in _all_figures(mini_run).items():
        assert figures.label_check(fig, TZ) == [], name


def test_dispatch_figure_content(mini_run: Path) -> None:
    fig = _all_figures(mini_run)["dispatch"]
    # 31 Mar (23 h) + 1 Apr (24 h) = 47 intervals; charge drawn negative.
    price = next(t for t in fig.data if t.name == "realised price")
    charge = next(t for t in fig.data if t.name == "charge")
    assert len(price.x) == 47 and all(v <= 0 for v in charge.y)


def test_label_check_detects_problems() -> None:
    bad = go.Figure(go.Scatter(x=[1], y=[1]))
    bad.update_layout(title="Price", yaxis_title="Price")
    assert set(figures.label_check(bad, TZ)) == {
        "title lacks timezone",
        "title lacks time range",
        "yaxis title lacks unit",
    }


def test_app_contains_no_modelling_code() -> None:
    """Spec §5 / acceptance: the UI only reads saved runs."""
    forbidden = (
        "gridflex.optimization",
        "gridflex.backtest",
        "gridflex.forecasting",
        "gridflex.battery",
        "gridflex.finance",
        "gridflex.settlement",
        "gridflex.data",
        "pulp",
        "highspy",
        "sklearn",
    )
    for path in (REPO_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(forbidden), f"{path.name} imports {n}"


def _app(run_dir: Path) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["run_dir"] = str(run_dir)
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(mini_run: Path, page: str) -> None:
    at = _app(mini_run)
    assert not at.exception
    at.switch_page(f"app_pages/{page}.py").run()
    assert not at.exception, at.exception
    assert not [e for e in at.error], [e.value for e in at.error]  # no labelling errors


def test_overview_shows_strategies_and_warnings(mini_run: Path) -> None:
    at = _app(mini_run)
    labels = [m.label for m in at.metric]
    assert labels == [
        "perfect_foresight",
        "forecast_persistence",
        "heuristic_persistence",
    ]
    assert any("upper benchmark" in w.value for w in at.warning)


def test_dispatch_strategy_selection(mini_run: Path) -> None:
    at = _app(mini_run)
    at.switch_page("app_pages/dispatch.py").run()
    at.selectbox(key="disp_strategy").select("heuristic_persistence").run()
    assert not at.exception


def test_pages_explain_missing_outputs(tmp_path: Path) -> None:
    at = _app(tmp_path)
    assert not at.exception
    assert any(
        "No backtest" in (i.title or "") or "No backtest" in i.value for i in at.info
    )


def test_config_and_metadata_exports(mini_run: Path) -> None:
    import yaml

    from ui_common import config_yaml, metadata_json

    meta = json.loads((mini_run / "backtest" / "metadata.json").read_text())
    assert yaml.safe_load(config_yaml(meta))["battery"]["energy_capacity_mwh"] == 20.0
    assert (
        json.loads(metadata_json(meta))["config_fingerprint"]
        == meta["config_fingerprint"]
    )
