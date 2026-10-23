"""Investment metrics, computed only when every required input is configured.

Definitions (P = discharge power in kW, E = nameplate energy in kWh):

* ``capex = capex_eur_per_kwh * E + capex_eur_per_kw * P`` (the power term
  is 0 if unset, i.e. included in the per-kWh figure).
* Annual operating cash flow
  ``A = annualised net operating revenue - fixed_om_eur_per_kw_year * P``.
  It assumes the backtest's annualised revenue recurs every year: no market
  change, capacity fade or inflation.
* **Simple payback** ``= capex / A`` (undiscounted; ``None`` if ``A <= 0``).
* **NPV** ``= -capex + sum_{y=1..H} CF_y / (1 + r)^y``, with
  ``CF_y = A`` when replacement is ``covered_by_degradation_cost``. With
  ``explicit`` it is ``CF_y = A + annualised degradation cost`` (the
  throughput charge is added back) and ``replacement_cost * E`` is
  subtracted in ``replacement_year``, so cell wear is never counted twice.

These are not IRR or profit. Grid charges, taxes, financing, merchant risk and
any revenue outside the modelled day-ahead strategy are excluded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gridflex.config.models import BatteryConfig, InvestmentConfig
from gridflex.finance.metrics import FinancialSummary


@dataclass(frozen=True)
class InvestmentResult:
    payback_years: float | None
    payback_missing: tuple[str, ...]
    npv_eur: float | None
    npv_missing: tuple[str, ...]
    capex_eur: float | None = None
    annual_cash_flow_eur: float | None = None
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def payback_computed(self) -> bool:
        return not self.payback_missing

    @property
    def npv_computed(self) -> bool:
        return not self.npv_missing

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _missing(inv: InvestmentConfig, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(n for n in names if getattr(inv, n) is None)


def investment_metrics(
    summary: FinancialSummary, battery: BatteryConfig, inv: InvestmentConfig
) -> InvestmentResult:
    pay_missing = _missing(inv, ("capex_eur_per_kwh", "fixed_om_eur_per_kw_year"))
    npv_missing = pay_missing + _missing(
        inv, ("horizon_years", "discount_rate", "replacement")
    )
    if pay_missing:
        return InvestmentResult(None, pay_missing, None, npv_missing)

    assert (
        inv.capex_eur_per_kwh is not None and inv.fixed_om_eur_per_kw_year is not None
    )
    p_kw = battery.discharge_power_mw * 1000.0
    e_kwh = battery.energy_capacity_mwh * 1000.0
    capex = inv.capex_eur_per_kwh * e_kwh + (inv.capex_eur_per_kw or 0.0) * p_kw
    annual = summary.annualised_net_eur - inv.fixed_om_eur_per_kw_year * p_kw
    payback = capex / annual if annual > 0 else None
    assumptions = [
        f"annual revenue = backtest net annualised ({summary.annualisation}), "
        f"{summary.days} days {summary.start_day}..{summary.end_day_inclusive}"
        + (" [partial year: extrapolated]" if summary.partial_year else ""),
        "revenue recurs unchanged every year (no market change, fade, inflation)",
        "simple payback is undiscounted; not an IRR",
    ]
    npv = None
    if not npv_missing:
        assert inv.horizon_years is not None and inv.discount_rate is not None
        r, horizon = inv.discount_rate, inv.horizon_years
        flows = [annual] * horizon
        if inv.replacement == "explicit":
            assert inv.replacement_year is not None
            assert inv.replacement_cost_eur_per_kwh is not None
            flows = [f + summary.annualised_degradation_eur for f in flows]
            flows[inv.replacement_year - 1] -= inv.replacement_cost_eur_per_kwh * e_kwh
            assumptions.append(
                "degradation charge added back; explicit replacement in year "
                f"{inv.replacement_year}"
            )
        else:
            assumptions.append("cell replacement covered by the degradation charge")
        npv = -capex + sum(cf / (1.0 + r) ** (y + 1) for y, cf in enumerate(flows))
    return InvestmentResult(
        payback, (), npv, npv_missing, capex, annual, tuple(assumptions)
    )
