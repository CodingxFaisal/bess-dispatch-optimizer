"""Cross-check: v0.1 MILP schedules satisfy the independent physics checker.

The MILP (gridflex.optimize, v0.1) and the checker (gridflex.battery) are
separate implementations. Agreement verifies both the MILP constraints and the
DispatchParams bridge's convention conversions (DC degradation and EFC bases).
"""

import numpy as np
import pytest

from gridflex.battery import BatteryModel, Schedule, check_schedule, throughput
from gridflex.config import GridFlexConfig
from gridflex.optimize import optimize_dispatch, settle
from gridflex.optimize.params import DispatchParams


def _v01_conventions(gf_cfg: GridFlexConfig) -> GridFlexConfig:
    b = gf_cfg.battery
    return gf_cfg.model_copy(
        update={
            "battery": b.model_copy(
                update={
                    "efc_basis": "ac_discharge_per_nameplate",
                    "degradation": b.degradation.model_copy(
                        update={"cost_basis": "ac_discharge"}
                    ),
                }
            )
        }
    )


@pytest.mark.parametrize("conventions", ["default", "v01"])
@pytest.mark.parametrize("seed", range(6))
def test_milp_output_is_physically_feasible(
    gf_cfg: GridFlexConfig, conventions: str, seed: int
) -> None:
    cfg = gf_cfg if conventions == "default" else _v01_conventions(gf_cfg)
    params = DispatchParams.from_config(cfg)
    battery = BatteryModel.from_config(cfg.battery)
    rng = np.random.default_rng(seed)
    n = 25 if seed % 2 else 24  # include a 25-hour (DST) day
    prices = rng.normal(40, 120, n)  # many negative prices
    res = optimize_dispatch(prices, params)
    assert res.solved
    sched = Schedule(res.p_ch, res.p_dis, 1.0)
    report = check_schedule(battery, sched, tol_mw=1e-5, tol_mwh=1e-5)
    assert report.ok, report.summary()
    # Degradation cost: v0.1 charges c_ac per AC MWh, physics c per configured
    # basis -- the bridge must make them equal.
    v01 = settle(res, prices, params)
    assert v01.degradation_cost_eur == pytest.approx(
        throughput(battery, sched).degradation_cost_eur, rel=1e-9, abs=1e-9
    )


def test_cycle_limit_binds_at_physical_limit(gf_cfg: GridFlexConfig) -> None:
    """Alternating prices push the MILP to the cycle cap; the checker agrees."""
    params = DispatchParams.from_config(gf_cfg)
    battery = BatteryModel.from_config(gf_cfg.battery)
    prices = np.tile([10.0, 300.0], 12)
    res = optimize_dispatch(prices, params)
    sched = Schedule(res.p_ch, res.p_dis, 1.0)
    report = check_schedule(battery, sched, tol_mw=1e-5, tol_mwh=1e-5)
    assert report.ok, report.summary()
    assert report.throughput is not None
    # Cap: 1.5 EFC per day on the DC-discharge / usable-energy basis.
    assert report.throughput.efc == pytest.approx(1.5, abs=1e-6)
