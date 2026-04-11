"""BESS day-ahead dispatch optimizer.

A grid-scale battery revenue optimizer for the German day-ahead market,
built around three questions a real BESS owner asks:

  1. What is the theoretical revenue ceiling (perfect foresight)?
  2. How much of it survives real forecast error (rolling-horizon backtest)?
  3. How do duration, efficiency and degradation cost drive the economics?

See the README for the full narrative.
"""

from bess.config import BatteryConfig, load_config

__all__ = ["BatteryConfig", "load_config"]
__version__ = "0.1.0"
