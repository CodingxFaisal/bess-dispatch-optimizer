"""Assemble validated market datasets from SMARD.

A :class:`MarketDataset` covers whole local delivery days ``[start_day,
end_day)`` at one day-ahead market time unit. It holds cleaned values on the
complete UTC grid (NaN where the source has no value), per-series provenance
and a validation report per series. No value is ever filled.

A window must lie inside a single MTU regime of the configured market, at that
regime's resolution: hourly before 2025-10-01, quarter-hourly from then on.
Hourly prices after the switch are averages of four separately traded
15-minute prices and are rejected rather than silently used.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from gridflex.config.models import GridFlexConfig, Resolution
from gridflex.data.clean import CleanSeries, clean_series
from gridflex.data.errors import DataValidationError, MarketRegimeError
from gridflex.data.smard import SmardClient
from gridflex.data.timeutils import expected_index, local_window_utc, to_local
from gridflex.data.validate import Severity, ValidationReport, validate_series

ATTRIBUTION = "Bundesnetzagentur | SMARD.de"
LICENSE = "CC BY 4.0"


@dataclass(frozen=True)
class SeriesMetadata:
    key: str
    smard_id: int
    description: str
    nature: str
    unit: str
    raw_unit: str
    resolution: str
    region: str
    n_chunks: int
    n_chunks_downloaded: int
    n_chunks_stale: int
    retrieved_from_utc: str
    retrieved_to_utc: str
    source_created_latest_utc: str | None
    duplicates_collapsed: int
    availability_rule: str
    attribution: str = ATTRIBUTION
    license: str = LICENSE

    @classmethod
    def from_clean(cls, clean: CleanSeries, cfg: GridFlexConfig) -> SeriesMetadata:
        spec = cfg.data.series[clean.key]
        chunks = clean.raw.chunks
        retrieved = sorted(c.meta.retrieved_at_utc for c in chunks) or [""]
        created = sorted(
            c.meta.source_created_utc for c in chunks if c.meta.source_created_utc
        )
        rule = spec.availability
        return cls(
            key=clean.key,
            smard_id=spec.smard_id,
            description=spec.description,
            nature=spec.nature,
            unit=clean.unit,
            raw_unit=spec.raw_unit,
            resolution=clean.resolution,
            region=clean.raw.region,
            n_chunks=len(chunks),
            n_chunks_downloaded=sum(c.downloaded for c in chunks),
            n_chunks_stale=sum(c.stale for c in chunks),
            retrieved_from_utc=retrieved[0],
            retrieved_to_utc=retrieved[-1],
            source_created_latest_utc=created[-1] if created else None,
            duplicates_collapsed=clean.duplicates_dropped,
            availability_rule=(
                f"D{rule.day_offset:+d} {rule.local_time.isoformat('minutes')} "
                f"{cfg.market.timezone}"
            ),
        )


@dataclass(frozen=True)
class MarketDataset:
    frame: pd.DataFrame
    resolution: Resolution
    timezone: str
    start_day: dt.date
    end_day: dt.date
    metadata: dict[str, SeriesMetadata]
    reports: dict[str, ValidationReport]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports.values())

    @property
    def interval_hours(self) -> float:
        return 1.0 if self.resolution == "hour" else 0.25

    def local_frame(self) -> pd.DataFrame:
        """Same data with the index shown in the market timezone."""
        out = self.frame.copy()
        out.index = to_local(self.frame.index, self.timezone).rename("timestamp_local")
        return out

    def to_legacy_frame(self) -> pd.DataFrame:
        """Frame for the v0.1 forecasters/backtest (local index named ``timestamp``)."""
        out = self.local_frame()
        out.index.name = "timestamp"
        return out

    def provenance(self) -> dict[str, Any]:
        """JSON-serialisable description for run metadata and exports."""
        from dataclasses import asdict

        return {
            "start_day": self.start_day.isoformat(),
            "end_day_exclusive": self.end_day.isoformat(),
            "resolution": self.resolution,
            "timezone_display": self.timezone,
            "timestamp_convention": "UTC interval start",
            "series": {k: asdict(v) for k, v in self.metadata.items()},
            "validation": {
                k: {"ok": r.ok, "issues": [asdict(i) for i in r.issues]}
                for k, r in self.reports.items()
            },
        }


def check_regime(
    cfg: GridFlexConfig, start_day: dt.date, end_day: dt.date, resolution: Resolution
) -> None:
    """Raise :class:`MarketRegimeError` unless every day uses ``resolution``."""
    market = cfg.market
    day = start_day
    while day < end_day:
        regime = market.regime_for(day)
        if regime.resolution != resolution:
            raise MarketRegimeError(
                f"delivery day {day} falls in the {regime.resolution} MTU regime "
                f"(from {regime.start_delivery_day}); requested {resolution}. "
                "Split the window at the regime boundary."
            )
        day += dt.timedelta(days=1)


def price_limit_series(
    cfg: GridFlexConfig, grid: pd.DatetimeIndex
) -> tuple[pd.Series, pd.Series]:
    days = grid.tz_convert(cfg.market.timezone).date
    limits = [cfg.market.price_limit_for(d) for d in days]
    lower = pd.Series([lim.min_eur_per_mwh for lim in limits], index=grid)
    upper = pd.Series([lim.max_eur_per_mwh for lim in limits], index=grid)
    return lower, upper


def build_market_dataset(
    cfg: GridFlexConfig,
    client: SmardClient,
    start_day: dt.date,
    end_day: dt.date,
    *,
    series: Sequence[str] = ("price",),
    resolution: Resolution | None = None,
    strict: bool = True,
    refresh: bool = False,
) -> MarketDataset:
    """Fetch, clean and validate ``series`` for local days ``[start_day, end_day)``.

    ``resolution`` defaults to the MTU regime of ``start_day``. With
    ``strict=True`` any validation error raises :class:`DataValidationError`
    (missing or null prices are errors; gaps in other series are warnings).
    """
    unknown = [k for k in series if k not in cfg.data.series]
    if unknown:
        raise KeyError(
            f"unknown series {unknown}; configured: {sorted(cfg.data.series)}"
        )
    res: Resolution = resolution or cfg.market.regime_for(start_day).resolution
    check_regime(cfg, start_day, end_day, res)

    tz = cfg.market.timezone
    start_utc, end_utc = local_window_utc(start_day, end_day, tz)
    grid = expected_index(start_utc, end_utc, res)

    columns: dict[str, pd.Series] = {}
    metadata: dict[str, SeriesMetadata] = {}
    reports: dict[str, ValidationReport] = {}
    for key in series:
        spec = cfg.data.series[key]
        raw = client.fetch(spec.smard_id, res, start_utc, end_utc, refresh=refresh)
        clean = clean_series(raw, key, spec)
        severity: Severity = "error" if spec.kind == "price" else "warning"
        lower: pd.Series | float | None
        upper: pd.Series | float | None
        if spec.kind == "price":
            lower, upper = price_limit_series(cfg, grid)
        else:
            lower, upper = spec.validation_min, spec.validation_max
        reports[key] = validate_series(
            clean.values,
            subject=f"{key} (SMARD {spec.smard_id}, {clean.unit})",
            start_utc=start_utc,
            end_utc=end_utc,
            resolution=res,
            timezone=tz,
            missing_severity=severity,
            lower=lower,
            upper=upper,
            duplicates_collapsed=clean.duplicates_dropped,
        )
        columns[key] = clean.values.reindex(grid)
        metadata[key] = SeriesMetadata.from_clean(clean, cfg)

    frame = pd.DataFrame(columns, index=grid)
    dataset = MarketDataset(frame, res, tz, start_day, end_day, metadata, reports)
    if strict and not dataset.ok:
        raise DataValidationError([r for r in reports.values() if not r.ok])
    return dataset
