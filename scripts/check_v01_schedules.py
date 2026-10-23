"""Verify v0.1 backtest schedules with the independent battery checker.

    uv run python scripts/check_v01_schedules.py --start 2023-01-01 --end 2025-01-01

Runs the v0.1 perfect-foresight backtest on validated SMARD data, then checks
every delivery day's schedule with :func:`gridflex.battery.check_schedule`,
carrying the end-of-day energy forward. Also compares the MILP's own SoC
trajectory with the physics module's state equation. Use ``--conventions v01``
to run with the v0.1 AC/nameplate degradation and cycle bases.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from _common import legacy_frame, load_cfg, load_dataset
from gridflex.backtest import run_backtest
from gridflex.battery import BatteryModel, Schedule, check_schedule, energy_trajectory
from gridflex.config import GridFlexConfig
from gridflex.forecast import get_forecaster
from gridflex.optimize.params import DispatchParams


def with_v01_conventions(cfg: GridFlexConfig) -> GridFlexConfig:
    b = cfg.battery
    deg = b.degradation.model_copy(update={"cost_basis": "ac_discharge"})
    battery = b.model_copy(
        update={"efc_basis": "ac_discharge_per_nameplate", "degradation": deg}
    )
    return cfg.model_copy(update={"battery": battery})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument("--conventions", choices=["default", "v01"], default="default")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    if args.conventions == "v01":
        cfg = with_v01_conventions(cfg)
    ds = load_dataset(cfg, args.start, args.end, offline=args.offline or None)
    params = DispatchParams.from_config(cfg)
    battery = BatteryModel.from_config(cfg.battery)
    res = run_backtest(
        legacy_frame(ds), params, get_forecaster("perfect"), args.start, args.end
    )

    sched = res.schedule.copy()
    sched.index = sched.index.tz_convert("UTC")
    days = pd.Index(res.schedule.index.normalize())
    # Each day is checked from the start energy the MILP was given (the v0.1
    # backtest carries the solver-reported SoC). The state-equation trajectory
    # is carried separately to measure how far the two accountings drift.
    e_milp = e_phys = battery.initial_energy_mwh
    n_ok = n_bad = 0
    max_soc_gap = 0.0
    efc = []
    for day in days.unique():
        part = sched[days == day]
        end = part.index[-1] + pd.Timedelta(hours=1)
        s = Schedule.from_index(
            part.index, end, part["p_charge_mw"], part["p_discharge_mw"]
        )
        report = check_schedule(battery, s, e_start_mwh=e_milp)  # tol 1e-6
        if report.ok:
            n_ok += 1
        else:
            n_bad += 1
            print(day.date(), report.summary())
        e = energy_trajectory(battery, s, e_milp)
        max_soc_gap = max(max_soc_gap, float(np.abs(e - part["soc_mwh"]).max()))
        assert report.throughput is not None
        efc.append(report.throughput.efc)
        e_phys = float(energy_trajectory(battery, s, e_phys)[-1])
        e_milp = float(part["soc_mwh"].iloc[-1])

    print(
        f"conventions={args.conventions}  days={n_ok + n_bad}  feasible={n_ok}  "
        f"infeasible={n_bad}  (tolerance 1e-6 MW / MWh)"
    )
    print(f"max per-day |SoC(MILP) - SoC(state equation)| = {max_soc_gap:.3e} MWh")
    print(
        f"end-of-period drift, state equation vs solver-reported SoC: "
        f"{e_phys - e_milp:.3e} MWh"
    )
    print(
        f"EFC/day ({battery.efc_basis}): mean {np.mean(efc):.3f}, "
        f"max {np.max(efc):.4f}, limit {battery.max_efc_per_day} per 24 h"
    )
    print(
        f"net revenue, perfect foresight: EUR {res.net_revenue_eur:,.0f} "
        f"({res.net_revenue_eur * 365 / res.n_days / params.power_mw:,.0f}/MW-yr)"
    )


if __name__ == "__main__":
    main()
