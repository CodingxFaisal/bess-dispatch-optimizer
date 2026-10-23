"""Financial metrics, investment metrics and sensitivity analysis."""

from gridflex.finance.investment import InvestmentResult, investment_metrics
from gridflex.finance.metrics import (
    ANNUALISATION,
    FinancialSummary,
    capture_by_period,
    period_table,
    summarise,
)
from gridflex.finance.sensitivity import CachedForecaster, run_sensitivity, variants

__all__ = [
    "ANNUALISATION",
    "CachedForecaster",
    "FinancialSummary",
    "InvestmentResult",
    "capture_by_period",
    "investment_metrics",
    "period_table",
    "run_sensitivity",
    "summarise",
    "variants",
]
