"""Rolling day-ahead backtest: strategies, heuristic baseline and engine.

See ``docs/BACKTEST_METHODOLOGY.md`` §2.
"""

from gridflex.backtest.engine import (
    BacktestAbortedError,
    BacktestResult,
    run_backtest,
    run_strategy,
    summary_table,
)
from gridflex.backtest.heuristic import two_window_schedule
from gridflex.backtest.strategies import (
    DayContext,
    DayPlan,
    ForecastStrategy,
    HeuristicStrategy,
    PerfectForesightStrategy,
    Strategy,
    make_forecaster,
    make_strategy,
)

__all__ = [
    "BacktestAbortedError",
    "BacktestResult",
    "DayContext",
    "DayPlan",
    "ForecastStrategy",
    "HeuristicStrategy",
    "PerfectForesightStrategy",
    "Strategy",
    "make_forecaster",
    "make_strategy",
    "run_backtest",
    "run_strategy",
    "summary_table",
    "two_window_schedule",
]
