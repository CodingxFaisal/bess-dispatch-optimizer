"""Compare the Stage 4 optimiser with the v0.1 MILP on real day-ahead prices.

    uv run python scripts/compare_optimizers.py --start 2023-01-01 --end 2025-01-01

For every local delivery day, solves the perfect-foresight dispatch (realised
prices, daily cyclic terminal policy) with

* ``gridflex.optimization`` (configured solver, HiGHS by default), starting
  each day from the state-equation energy of the previous day's schedule; and
* the v0.1 MILP via the DispatchParams bridge (CBC).

Reports solve status counts, objective agreement, MIP gaps and runtimes, and
writes per-day solve reports to ``runs/latest/optimizer_comparison.csv``.
This is a solver diagnostic, not a strategy result.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from _common import RUNS, load_cfg, load_dataset
from gridflex.battery import BatteryModel
from gridflex.optimization import DispatchProblem, solve_dispatch
from gridflex.optimize import optimize_dispatch, settle
from gridflex.optimize.params import DispatchParams


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument("--solver", choices=["highs", "cbc"], default=None)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    opts = cfg.optimization
    if args.solver:
        opts = opts.model_copy(update={"solver": args.solver})
    ds = load_dataset(
        cfg, args.start, args.end, series=("price",), offline=args.offline or None
    )
    battery = BatteryModel.from_config(cfg.battery)
    params = DispatchParams.from_config(cfg)

    price = ds.frame["price"]
    local_days = pd.Index(price.index.tz_convert(cfg.market.timezone).date)
    rows = []
    e_start = battery.initial_energy_mwh
    t_v01 = 0.0
    for day in sorted(set(local_days)):
        p = price[local_days == day]
        end = p.index[-1] + pd.Timedelta(hours=ds.interval_hours)
        problem = DispatchProblem.from_index(
            battery, p.index, end, p.to_numpy(), e_start_mwh=e_start, label=str(day)
        )
        sol = solve_dispatch(problem, opts)
        t0 = time.perf_counter()
        old = optimize_dispatch(p.to_numpy(), params)
        t_v01 += time.perf_counter() - t0
        old_obj = (
            settle(old, p.to_numpy(), params).net_revenue_eur if old.solved else np.nan
        )
        r = sol.report
        rows.append(
            {
                "delivery_day": day,
                "intervals": problem.n_intervals,
                "status": r.status,
                "success": r.success,
                "objective_eur": r.objective_check_eur,
                "v01_objective_eur": old_obj,
                "mip_gap": r.mip_gap,
                "runtime_s": r.runtime_s,
                "e_start_mwh": e_start,
            }
        )
        if sol.success:
            assert sol.energy_mwh is not None
            e_start = float(sol.energy_mwh[-1])  # physically carried state

    df = pd.DataFrame(rows)
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / "optimizer_comparison.csv"
    df.to_csv(out, index=False)

    ok = df[df["success"]]
    diff = (ok["objective_eur"] - ok["v01_objective_eur"]).abs()
    print(
        f"solver: {opts.solver}; days {len(df)}; status counts "
        f"{df['status'].value_counts().to_dict()}"
    )
    lengths = df["intervals"].value_counts().sort_index().to_dict()
    print(f"day lengths (intervals): {lengths}")
    print(
        f"total objective: new {ok['objective_eur'].sum():,.2f} EUR | "
        f"v0.1 {ok['v01_objective_eur'].sum():,.2f} EUR"
    )
    print(f"max per-day |new - v0.1| = {diff.max():.3e} EUR")
    gaps = ok["mip_gap"].dropna()
    if len(gaps):
        print(f"MIP gap: max {gaps.max():.2e}, mean {gaps.mean():.2e}")
    print(
        f"runtime: new {df['runtime_s'].sum():.1f} s total "
        f"(max {df['runtime_s'].max():.3f} s/day) | v0.1 {t_v01:.1f} s total"
    )
    print(
        f"start energy range carried: [{df['e_start_mwh'].min():.9f}, "
        f"{df['e_start_mwh'].max():.9f}] MWh"
    )
    print(f"per-day reports -> {out}")


if __name__ == "__main__":
    main()
