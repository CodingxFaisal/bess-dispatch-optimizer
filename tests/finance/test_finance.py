"""Financial and investment metrics against hand calculations."""

import datetime as dt
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from gridflex.config import ConfigError, GridFlexConfig, InvestmentConfig, load_config
from gridflex.finance import (
    capture_by_period,
    investment_metrics,
    period_table,
    summarise,
)
from tests_helpers import DEFAULT_CONFIG


def _daily(
    net: list[float], start: str = "2024-01-30", **cols: list[float]
) -> pd.DataFrame:
    days = pd.date_range(start, periods=len(net), freq="D").date
    base: dict[str, Any] = {
        "delivery_day": days,
        "strategy": "s",
        "status": "ok",
        "net_operating_revenue_eur": net,
        "gross_revenue_eur": cols.get("gross", net),
        "degradation_cost_eur": cols.get("deg", [0.0] * len(net)),
        "trading_cost_eur": [0.0] * len(net),
        "energy_bought_mwh": cols.get("bought", [1.0] * len(net)),
        "energy_sold_mwh": cols.get("sold", [0.8] * len(net)),
        "equivalent_full_cycles": cols.get("efc", [0.5] * len(net)),
    }
    return pd.DataFrame(base)


def test_summary_hand_values(gf_cfg: GridFlexConfig) -> None:
    s = summarise(
        _daily([100.0, -20.0, 50.0], gross=[150.0, 0.0, 80.0]), gf_cfg.battery
    )
    assert s.days == 3 and s.partial_year and s.negative_net_days == 1
    assert s.net_operating_revenue_eur == 130.0 and s.gross_revenue_eur == 230.0
    # 130 * 365 / 3 = 15 816.67; / 10 MW = 1 581.67; / 20 MWh = 790.83.
    assert s.annualised_net_eur == pytest.approx(130 * 365 / 3)
    assert s.net_per_mw_year_eur == pytest.approx(130 * 365 / 3 / 10)
    assert s.net_per_mwh_year_eur == pytest.approx(130 * 365 / 3 / 20)
    assert s.efc_per_day == pytest.approx(0.5)
    assert s.mean_daily_net_eur == pytest.approx(130 / 3)
    assert s.start_day == dt.date(2024, 1, 30) and s.end_day_inclusive == dt.date(
        2024, 2, 1
    )
    assert "365" in s.annualisation


def test_full_year_not_partial(gf_cfg: GridFlexConfig) -> None:
    s = summarise(_daily([1.0] * 366, start="2024-01-01"), gf_cfg.battery)
    assert not s.partial_year
    assert s.annualised_net_eur == pytest.approx(365.0)  # 366 * 365 / 366


def test_capture_ratio_definition(gf_cfg: GridFlexConfig) -> None:
    s = summarise(
        _daily([100.0, -20.0, 50.0], gross=[150.0, 0.0, 80.0]),
        gf_cfg.battery,
        benchmark=_daily([200.0, 100.0, 100.0], gross=[300.0, 150.0, 150.0]),
    )
    assert s.capture_ratio == pytest.approx(130 / 400)
    assert s.gross_capture_ratio == pytest.approx(230 / 600)
    bad = summarise(_daily([1.0]), gf_cfg.battery, benchmark=_daily([-5.0]))
    assert bad.capture_ratio is not None and math.isnan(bad.capture_ratio)
    with pytest.raises(ValueError, match="same delivery days"):
        summarise(_daily([1.0, 2.0]), gf_cfg.battery, benchmark=_daily([1.0]))


def test_summary_input_errors(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(ValueError, match="no daily rows"):
        summarise(_daily([]), gf_cfg.battery)
    dup = pd.concat([_daily([1.0]), _daily([1.0])])
    with pytest.raises(ValueError, match="one row per delivery day"):
        summarise(dup, gf_cfg.battery)


def test_period_tables_flag_partial_periods(gf_cfg: GridFlexConfig) -> None:
    d = _daily([100.0, -20.0, 50.0])  # 30-31 Jan, 1 Feb 2024
    m = period_table(d, gf_cfg.battery, "month").set_index("period")
    assert m.loc["2024-01", "days"] == 2 and m.loc["2024-01", "calendar_days"] == 31
    assert m.loc["2024-02", "calendar_days"] == 29  # leap year
    assert not m["complete"].any()
    assert m.loc["2024-01", "net_operating_revenue_eur"] == 80.0
    assert m.loc["2024-01", "net_per_mw_eur"] == 8.0
    assert m.loc["2024-01", "negative_net_days"] == 1
    y = period_table(_daily([1.0] * 366, start="2024-01-01"), gf_cfg.battery, "year")
    assert bool(y["complete"].iloc[0]) and y["calendar_days"].iloc[0] == 366
    c = capture_by_period(d, _daily([200.0, 100.0, 100.0]), "month").set_index("period")
    assert c.loc["2024-01", "capture_ratio"] == pytest.approx(80 / 300)


# --------------------------------------------------------------------------- #
# Investment                                                                  #
# --------------------------------------------------------------------------- #
def _year_summary(gf_cfg: GridFlexConfig, deg: float = 0.0):  # type: ignore[no-untyped-def]
    # 365 days of 100 EUR net -> 36 500 EUR/yr; degradation deg EUR/day.
    return summarise(
        _daily([100.0] * 365, start="2023-01-01", deg=[deg] * 365), gf_cfg.battery
    )


def test_investment_not_computed_without_inputs(gf_cfg: GridFlexConfig) -> None:
    r = investment_metrics(
        _year_summary(gf_cfg), gf_cfg.battery, gf_cfg.economics.investment
    )
    assert r.payback_years is None and r.npv_eur is None
    assert r.payback_missing == ("capex_eur_per_kwh", "fixed_om_eur_per_kw_year")
    assert "discount_rate" in r.npv_missing and not r.payback_computed


def test_payback_hand_value(gf_cfg: GridFlexConfig) -> None:
    inv = InvestmentConfig(capex_eur_per_kwh=250.0, fixed_om_eur_per_kw_year=1.0)
    r = investment_metrics(_year_summary(gf_cfg), gf_cfg.battery, inv)
    # capex 250 * 20 000 kWh = 5 000 000; A = 36 500 - 1 * 10 000 kW = 26 500.
    assert r.capex_eur == 5_000_000 and r.annual_cash_flow_eur == 26_500
    assert r.payback_years == pytest.approx(5_000_000 / 26_500)
    assert r.npv_eur is None and set(r.npv_missing) == {
        "horizon_years",
        "discount_rate",
        "replacement",
    }
    assert not any("partial year" in a for a in r.assumptions)  # full year


def test_payback_none_when_cash_flow_not_positive(gf_cfg: GridFlexConfig) -> None:
    inv = InvestmentConfig(capex_eur_per_kwh=250.0, fixed_om_eur_per_kw_year=10.0)
    assert (
        investment_metrics(_year_summary(gf_cfg), gf_cfg.battery, inv).payback_years
        is None
    )  # 36 500 - 100 000 < 0


def test_npv_hand_values(gf_cfg: GridFlexConfig) -> None:
    common = dict(
        capex_eur_per_kwh=250.0, capex_eur_per_kw=10.0, fixed_om_eur_per_kw_year=1.0
    )
    # capex = 5 000 000 + 10 * 10 000 = 5 100 000; A = 26 500.
    r0 = investment_metrics(
        _year_summary(gf_cfg),
        gf_cfg.battery,
        InvestmentConfig(
            **common,
            horizon_years=10,
            discount_rate=0.0,
            replacement="covered_by_degradation_cost",
        ),
    )
    assert r0.npv_eur == pytest.approx(-5_100_000 + 10 * 26_500)
    r5 = investment_metrics(
        _year_summary(gf_cfg),
        gf_cfg.battery,
        InvestmentConfig(
            **common,
            horizon_years=2,
            discount_rate=0.05,
            replacement="covered_by_degradation_cost",
        ),
    )
    assert r5.npv_eur == pytest.approx(-5_100_000 + 26_500 / 1.05 + 26_500 / 1.05**2)


def test_explicit_replacement_avoids_double_counting(gf_cfg: GridFlexConfig) -> None:
    # Degradation charge 10 EUR/day = 3 650 EUR/yr is added back; replacement
    # 100 EUR/kWh * 20 000 kWh = 2 000 000 in year 2.
    summary = _year_summary(gf_cfg, deg=10.0)
    inv = InvestmentConfig(
        capex_eur_per_kwh=250.0,
        fixed_om_eur_per_kw_year=1.0,
        horizon_years=3,
        discount_rate=0.0,
        replacement="explicit",
        replacement_year=2,
        replacement_cost_eur_per_kwh=100.0,
    )
    r = investment_metrics(summary, gf_cfg.battery, inv)
    assert r.npv_eur == pytest.approx(-5_000_000 + 3 * (26_500 + 3_650) - 2_000_000)


@pytest.mark.parametrize(
    "inv",
    [
        {"replacement": "explicit"},
        {
            "replacement": "explicit",
            "replacement_year": 30,
            "replacement_cost_eur_per_kwh": 50,
            "horizon_years": 20,
        },
        {"replacement_year": 5},
        {"discount_rate": 1.5},
        {"capex_eur_per_kwh": 0},
    ],
)
def test_invalid_investment_config(tmp_path: Path, inv: dict[str, Any]) -> None:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["economics"]["investment"].update(inv)
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
