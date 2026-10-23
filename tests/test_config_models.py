"""Configuration contract: the default loads, invalid inputs are rejected."""

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from gridflex.config import ConfigError, GridFlexConfig, config_fingerprint, load_config
from tests_helpers import DEFAULT_CONFIG


def _raw() -> dict[str, Any]:
    return yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _invalid(tmp_path: Path, mutate: Any) -> None:
    raw = _raw()
    mutate(raw)
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, raw))


# --------------------------------------------------------------------------- #
# Default configuration                                                       #
# --------------------------------------------------------------------------- #
def test_default_config_values(gf_cfg: GridFlexConfig) -> None:
    b = gf_cfg.battery
    assert b.charge_power_mw == 10.0 and b.discharge_power_mw == 10.0
    assert b.energy_capacity_mwh == 20.0
    # SoC window 5-95 % of 20 MWh: 1 and 19 MWh, usable 18 MWh (hand values).
    assert b.soc_min_mwh == pytest.approx(1.0)
    assert b.soc_max_mwh == pytest.approx(19.0)
    assert b.usable_energy_mwh == pytest.approx(18.0)
    assert b.soc_initial_mwh == pytest.approx(10.0)
    # sqrt(0.86) = 0.92736184954957 per leg; product recovers 0.86.
    assert b.efficiency.charge == pytest.approx(0.92736184954957, abs=1e-12)
    assert b.efficiency.charge * b.efficiency.discharge == pytest.approx(0.86)
    # 120 EUR/kWh * 1000 / (8000 * 0.9) = 120000 / 7200 = 16.666... EUR/MWh.
    assert b.degradation.cost_eur_per_mwh == pytest.approx(50.0 / 3.0)
    assert gf_cfg.economics.capex_eur_per_kwh is None
    assert gf_cfg.market.day_ahead_gate_closure_local == dt.time(12, 0)


def test_one_way_efficiency_mode(tmp_path: Path) -> None:
    raw = _raw()
    raw["battery"]["efficiency"] = {"mode": "one_way", "charge": 0.95, "discharge": 0.9}
    cfg = load_config(_write(tmp_path, raw))
    assert cfg.battery.efficiency.charge == 0.95
    assert cfg.battery.efficiency.discharge == 0.9
    assert cfg.battery.efficiency.round_trip == pytest.approx(0.855)


def test_mtu_regime_lookup(gf_cfg: GridFlexConfig) -> None:
    m = gf_cfg.market
    assert m.regime_for(dt.date(2018, 10, 1)).resolution == "hour"
    assert m.regime_for(dt.date(2025, 9, 30)).resolution == "hour"
    assert m.regime_for(dt.date(2025, 10, 1)).resolution == "quarterhour"
    with pytest.raises(ValueError, match="no entry"):
        m.regime_for(dt.date(2018, 9, 30))


def test_price_limit_lookup(gf_cfg: GridFlexConfig) -> None:
    m = gf_cfg.market
    assert m.price_limit_for(dt.date(2021, 1, 1)).max_eur_per_mwh == 3000.0
    assert m.price_limit_for(dt.date(2024, 1, 1)).max_eur_per_mwh == 4000.0
    assert m.price_limit_for(dt.date(2026, 5, 28)).min_eur_per_mwh == -500.0
    assert m.price_limit_for(dt.date(2026, 5, 29)).min_eur_per_mwh == -600.0


def test_series_units(gf_cfg: GridFlexConfig) -> None:
    s = gf_cfg.data.series
    assert s["price"].smard_id == 4169 and s["price"].clean_unit == "EUR/MWh"
    for key, sid in [
        ("load_forecast", 411),
        ("solar_forecast", 125),
        ("wind_onshore_forecast", 123),
        ("wind_offshore_forecast", 3791),
    ]:
        assert s[key].smard_id == sid
        assert s[key].raw_unit == "MWh per interval"
        assert s[key].clean_unit == "MW"


def test_fingerprint_is_stable_and_sensitive(
    gf_cfg: GridFlexConfig, tmp_path: Path
) -> None:
    again = load_config(DEFAULT_CONFIG)
    assert config_fingerprint(gf_cfg) == config_fingerprint(again)
    raw = _raw()
    raw["battery"]["energy_capacity_mwh"] = 40.0
    other = load_config(_write(tmp_path, raw))
    assert config_fingerprint(other) != config_fingerprint(gf_cfg)


def test_models_are_frozen(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(ValidationError):  # frozen instance
        gf_cfg.battery.charge_power_mw = 5.0  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Rejections                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["battery"].update(unknown_key=1),
        lambda r: r["battery"].update(charge_power_mw=0.0),
        lambda r: r["battery"].update(energy_capacity_mwh=-1.0),
        lambda r: r["battery"].update(soc_min_fraction=0.9, soc_max_fraction=0.1),
        lambda r: r["battery"].update(soc_initial_fraction=0.99),
        lambda r: r["battery"]["efficiency"].update(round_trip=1.2),
        lambda r: r["battery"].update(efficiency={"mode": "one_way", "charge": 0.9}),
        lambda r: r["battery"].update(efficiency={"mode": "magic", "round_trip": 0.9}),
        lambda r: r["battery"].update(terminal_soc_policy="fixed"),
        lambda r: r["battery"].update(terminal_soc_fraction=0.5),
        lambda r: r["battery"].update(
            terminal_soc_policy="fixed", terminal_soc_fraction=0.99
        ),
        lambda r: r["battery"].update(max_equivalent_full_cycles_per_day=0),
        lambda r: r["battery"]["degradation"].update(depth_of_discharge=0.0),
        lambda r: r["market"].update(timezone="Mars/Olympus"),
        lambda r: r["market"].update(
            mtu_regimes=list(reversed(r["market"]["mtu_regimes"]))
        ),
        lambda r: r["market"].update(mtu_regimes=[]),
        lambda r: r["market"]["price_limits"][0].update(min_eur_per_mwh=5000.0),
        lambda r: r["data"]["series"]["load_forecast"].update(raw_unit="EUR/MWh"),
        lambda r: r["data"]["series"].pop("price"),
        lambda r: r["data"]["http"].update(max_retries=-1),
        lambda r: r.update(schema_version=2),
    ],
)
def test_invalid_configs_rejected(tmp_path: Path, mutate: Any) -> None:
    _invalid(tmp_path, mutate)


def test_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("battery: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("42", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(scalar)


def test_fixed_terminal_policy_accepted(tmp_path: Path) -> None:
    raw = copy.deepcopy(_raw())
    raw["battery"].update(terminal_soc_policy="fixed", terminal_soc_fraction=0.5)
    raw.pop("backtest")  # the default heuristic strategy needs a cyclic policy
    cfg = load_config(_write(tmp_path, raw))
    assert cfg.battery.terminal_soc_fraction == 0.5
