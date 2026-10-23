"""Raw SMARD values -> cleaned series in documented units.

Cleaning does exactly three things and nothing else:

1. Duplicate timestamps: identical repeats are collapsed (and counted);
   conflicting repeats raise :class:`DataIntegrityError`.
2. Sorting by UTC timestamp.
3. Unit conversion: prices stay in EUR/MWh; energy per market time unit
   (MWh per interval) becomes mean power in MW by dividing by the interval
   length in hours.

Nulls stay NaN. Nothing is interpolated, filled or invented.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gridflex.config.models import SeriesConfig
from gridflex.data.errors import DataIntegrityError
from gridflex.data.smard import RawSeries
from gridflex.data.timeutils import interval_hours


@dataclass(frozen=True)
class CleanSeries:
    key: str
    values: pd.Series
    unit: str
    resolution: str
    duplicates_dropped: int
    raw: RawSeries


def collapse_duplicates(values: pd.Series, label: str) -> tuple[pd.Series, int]:
    """Drop identical duplicate timestamps; raise on conflicting values."""
    dup_mask = values.index.duplicated(keep=False)
    if not dup_mask.any():
        return values, 0
    dups = values[dup_mask]
    for ts, group in dups.groupby(level=0):
        arr = group.to_numpy(dtype="float64")
        same = np.all(np.isnan(arr)) or (
            not np.isnan(arr).any() and np.all(arr == arr[0])
        )
        if not same:
            raise DataIntegrityError(
                f"{label}: conflicting values {arr.tolist()} for timestamp {ts}"
            )
    deduped = values[~values.index.duplicated(keep="first")]
    return deduped, int(len(values) - len(deduped))


def clean_series(raw: RawSeries, key: str, spec: SeriesConfig) -> CleanSeries:
    if raw.series_id != spec.smard_id:
        raise ValueError(
            f"series {key!r} is SMARD {spec.smard_id}, got raw data for {raw.series_id}"
        )
    values, dropped = collapse_duplicates(raw.values, f"{key} (SMARD {raw.series_id})")
    values = values.sort_index().astype("float64")
    if spec.kind == "energy":
        values = values / interval_hours(raw.resolution)  # MWh per interval -> MW
    values.index.name = "timestamp_utc"
    return CleanSeries(
        key=key,
        values=values.rename(key),
        unit=spec.clean_unit,
        resolution=raw.resolution,
        duplicates_dropped=dropped,
        raw=raw,
    )
