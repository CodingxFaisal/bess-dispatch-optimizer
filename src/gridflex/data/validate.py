"""Data-quality validation for cleaned series.

Validation reports problems; it never repairs them. Each check yields a
:class:`ValidationIssue` with a stable ``code``:

==================== ======== =================================================
code                 severity meaning
==================== ======== =================================================
INDEX_NOT_UTC        error    index is not timezone-aware UTC
NOT_SORTED           error    timestamps not strictly increasing
DUPLICATE_TIMESTAMPS error    repeated timestamps remain
DUPLICATES_COLLAPSED info     identical repeats were collapsed during cleaning
OFF_GRID_TIMESTAMPS  error    timestamps outside the window or not on the grid
RESOLUTION_MISMATCH  error    observed interval length differs from expected
MISSING_INTERVALS    config.  expected intervals absent from the index
NULL_VALUES          config.  intervals present but NaN (null at the source)
INCOMPLETE_DAYS      config.  local delivery days with missing or null data
BELOW_MINIMUM        error    values below the plausibility bound
ABOVE_MAXIMUM        error    values above the plausibility bound
DST_DAYS             info     23 h / 25 h local days in the window
==================== ======== =================================================

"config." severities are chosen by the caller (errors for prices, warnings for
optional features).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from gridflex.data.errors import DataValidationError
from gridflex.data.timeutils import expected_index, interval_minutes, require_utc

Severity = Literal["error", "warning", "info"]
_MAX_EXAMPLES = 5


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    message: str
    count: int = 0
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    subject: str
    window_start_utc: str
    window_end_utc: str
    resolution: str
    n_expected: int
    n_valid: int
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self, severity: Severity | None = None) -> set[str]:
        return {
            i.code for i in self.issues if severity is None or i.severity == severity
        }

    def summary(self) -> str:
        head = (
            f"[{'OK' if self.ok else 'FAIL'}] {self.subject} {self.resolution} "
            f"{self.window_start_utc} -> {self.window_end_utc}: "
            f"{self.n_valid}/{self.n_expected} valid intervals"
        )
        lines = [head]
        for i in self.issues:
            ex = f" e.g. {', '.join(i.examples)}" if i.examples else ""
            lines.append(f"  {i.severity.upper():7s} {i.code}: {i.message}{ex}")
        return "\n".join(lines)

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise DataValidationError([self])


def _examples(items: pd.Index | list[object]) -> tuple[str, ...]:
    return tuple(str(x) for x in list(items)[:_MAX_EXAMPLES])


def validate_series(
    values: pd.Series,
    *,
    subject: str,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    resolution: str,
    timezone: str,
    missing_severity: Severity = "error",
    lower: pd.Series | float | None = None,
    upper: pd.Series | float | None = None,
    duplicates_collapsed: int = 0,
) -> ValidationReport:
    """Check ``values`` against the complete ``resolution`` grid of the window.

    ``lower``/``upper`` may be scalars or Series aligned to the expected grid
    (for time-varying limits such as day-ahead price bounds).
    """
    start = require_utc(start_utc, "start_utc")
    end = require_utc(end_utc, "end_utc")
    grid = expected_index(start, end, resolution)
    step = pd.Timedelta(minutes=interval_minutes(resolution))
    issues: list[ValidationIssue] = []
    idx = values.index

    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
        issues.append(
            ValidationIssue(
                "INDEX_NOT_UTC",
                "error",
                f"index must be tz-aware UTC, got {getattr(idx, 'tz', None)}",
            )
        )
        return ValidationReport(
            subject,
            start.isoformat(),
            end.isoformat(),
            resolution,
            len(grid),
            0,
            tuple(issues),
        )

    if duplicates_collapsed:
        issues.append(
            ValidationIssue(
                "DUPLICATES_COLLAPSED",
                "info",
                "identical duplicate timestamps collapsed during cleaning",
                duplicates_collapsed,
            )
        )
    dup = idx[idx.duplicated()]
    if len(dup):
        issues.append(
            ValidationIssue(
                "DUPLICATE_TIMESTAMPS",
                "error",
                "repeated timestamps",
                len(dup),
                _examples(dup),
            )
        )
    if not idx.is_monotonic_increasing:
        issues.append(
            ValidationIssue("NOT_SORTED", "error", "timestamps are not sorted")
        )

    unique_idx = idx.unique()
    off_grid = unique_idx.difference(grid)
    if len(off_grid):
        issues.append(
            ValidationIssue(
                "OFF_GRID_TIMESTAMPS",
                "error",
                f"timestamps outside the window or not on the {resolution} grid",
                len(off_grid),
                _examples(off_grid),
            )
        )

    if len(unique_idx) > 1:
        diffs = pd.Series(unique_idx.sort_values()).diff().dropna()
        modal = diffs.mode().iloc[0]
        finer = diffs[diffs < step]
        if modal != step or len(finer):
            issues.append(
                ValidationIssue(
                    "RESOLUTION_MISMATCH",
                    "error",
                    f"expected {step}, modal interval {modal}, "
                    f"{len(finer)} interval(s) shorter than expected",
                    len(finer),
                )
            )

    missing = grid.difference(unique_idx)
    if len(missing):
        issues.append(
            ValidationIssue(
                "MISSING_INTERVALS",
                missing_severity,
                "expected intervals absent",
                len(missing),
                _examples(missing),
            )
        )

    on_grid = values[~values.index.duplicated(keep="first")].reindex(grid)
    present_null = on_grid.isna() & grid.isin(unique_idx)
    if present_null.any():
        issues.append(
            ValidationIssue(
                "NULL_VALUES",
                missing_severity,
                "intervals present with null values",
                int(present_null.sum()),
                _examples(grid[present_null.to_numpy()]),
            )
        )

    local_days = pd.Index(grid.tz_convert(timezone).date)
    valid = on_grid.notna().to_numpy()
    per_day = pd.DataFrame({"day": local_days, "valid": valid}).groupby("day")["valid"]
    incomplete = per_day.all()
    bad_days = incomplete[~incomplete].index
    if len(bad_days):
        issues.append(
            ValidationIssue(
                "INCOMPLETE_DAYS",
                missing_severity,
                "local delivery days with missing or null intervals",
                len(bad_days),
                _examples(list(bad_days)),
            )
        )

    hours_per_day = per_day.size() * step / pd.Timedelta(hours=1)
    dst = hours_per_day[hours_per_day.isin([23.0, 25.0])]
    if len(dst):
        issues.append(
            ValidationIssue(
                "DST_DAYS",
                "info",
                "daylight-saving transition days in window: "
                + ", ".join(f"{d} ({h:g} h)" for d, h in dst.items()),
                len(dst),
            )
        )

    for bound, code, cmp in (
        (lower, "BELOW_MINIMUM", np.less),
        (upper, "ABOVE_MAXIMUM", np.greater),
    ):
        if bound is None:
            continue
        b = (
            bound.reindex(grid)
            if isinstance(bound, pd.Series)
            else pd.Series(bound, index=grid)
        )
        mask = cmp(on_grid.to_numpy(), b.to_numpy()) & on_grid.notna().to_numpy()
        if mask.any():
            bad = on_grid[mask]
            issues.append(
                ValidationIssue(
                    code,
                    "error",
                    "values outside the plausibility bound",
                    int(mask.sum()),
                    _examples([f"{t}={v:g}" for t, v in bad.items()]),
                )
            )

    return ValidationReport(
        subject=subject,
        window_start_utc=start.isoformat(),
        window_end_utc=end.isoformat(),
        resolution=resolution,
        n_expected=len(grid),
        n_valid=int(valid.sum()),
        issues=tuple(issues),
    )
