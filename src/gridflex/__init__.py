"""GridFlex: battery dispatch optimisation and day-ahead market research.

Package layout (see ``docs/AUDIT.md`` for the migration plan):

* ``gridflex.config``   typed configuration and domain models
* ``gridflex.data``     SMARD retrieval, raw cache, cleaning, validation,
                        information-availability registry
* ``gridflex.optimize`` / ``forecast`` / ``backtest`` / ``analysis``
                        v0.1 modules, retained until rewritten in Stages 3-7
"""

from gridflex.config import GridFlexConfig, load_config

__all__ = ["GridFlexConfig", "load_config"]
__version__ = "0.2.0.dev0"
