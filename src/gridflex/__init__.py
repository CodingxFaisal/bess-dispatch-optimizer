"""GridFlex: battery dispatch optimisation and day-ahead market research.

Package layout:

* ``gridflex.config``   typed configuration and domain models
* ``gridflex.data``     SMARD retrieval, raw cache, cleaning, validation,
                        information-availability registry
* ``gridflex.battery``      physics, throughput, feasibility checker
* ``gridflex.optimization`` dispatch MILP (PuLP + HiGHS) and solve reports
* ``gridflex.forecasting``  day-ahead price forecasts with leakage guards
* ``gridflex.settlement``   settlement of committed schedules
* ``gridflex.backtest``     rolling day-ahead backtest engine and strategies
"""

from gridflex.config import GridFlexConfig, load_config

__all__ = ["GridFlexConfig", "load_config"]
__version__ = "0.2.0.dev0"
