"""Validation reports problems without repairing them."""

import math

import pandas as pd
import pytest

from gridflex.data.errors import DataValidationError
from gridflex.data.timeutils import expected_index, local_window_utc
from gridflex.data.validate import validate_series

TZ = "Europe/Berlin"


def _window(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    return local_window_utc(pd.Timestamp(start).date(), pd.Timestamp(end).date(), TZ)


def _complete(start: str, end: str, resolution: str = "hour") -> pd.Series:
    s, e = _window(start, end)
    idx = expected_index(s, e, resolution)
    return pd.Series(range(len(idx)), index=idx, dtype="float64")


def _validate(values: pd.Series, start: str, end: str, resolution: str = "hour", **kw):
    s, e = _window(start, end)
    return validate_series(
        values,
        subject="t",
        start_utc=s,
        end_utc=e,
        resolution=resolution,
        timezone=TZ,
        **kw,
    )


def test_complete_series_is_ok() -> None:
    rep = _validate(_complete("2024-06-03", "2024-06-05"), "2024-06-03", "2024-06-05")
    assert rep.ok and rep.issues == () and rep.n_valid == rep.n_expected == 48


def test_dst_days_reported_with_23_and_25_hours() -> None:
    spring = _validate(
        _complete("2024-03-31", "2024-04-01"), "2024-03-31", "2024-04-01"
    )
    autumn = _validate(
        _complete("2024-10-27", "2024-10-28"), "2024-10-27", "2024-10-28"
    )
    assert spring.n_expected == 23 and autumn.n_expected == 25
    assert spring.ok and autumn.ok
    assert (
        "2024-03-31 (23 h)"
        in next(i for i in spring.issues if i.code == "DST_DAYS").message
    )
    assert (
        "2024-10-27 (25 h)"
        in next(i for i in autumn.issues if i.code == "DST_DAYS").message
    )


def test_quarterhour_dst_day_has_100_intervals() -> None:
    rep = _validate(
        _complete("2025-10-26", "2025-10-27", "quarterhour"),
        "2025-10-26",
        "2025-10-27",
        "quarterhour",
    )
    assert rep.ok and rep.n_expected == 100  # 25 h * 4


def test_missing_interval_is_error_and_not_filled() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    values = full.drop(full.index[5])
    rep = _validate(values, "2024-06-03", "2024-06-04")
    assert not rep.ok
    assert rep.codes("error") >= {"MISSING_INTERVALS", "INCOMPLETE_DAYS"}
    assert len(values) == 23  # input untouched
    with pytest.raises(DataValidationError, match="MISSING_INTERVALS"):
        rep.raise_for_errors()


def test_missing_as_warning_keeps_report_ok() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    rep = _validate(
        full.drop(full.index[5]), "2024-06-03", "2024-06-04", missing_severity="warning"
    )
    assert rep.ok and "MISSING_INTERVALS" in rep.codes("warning")


def test_null_values_reported() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    full.iloc[[2, 3]] = math.nan
    rep = _validate(full, "2024-06-03", "2024-06-04")
    issue = next(i for i in rep.issues if i.code == "NULL_VALUES")
    assert issue.count == 2 and issue.severity == "error"
    assert rep.n_valid == 22


def test_duplicates_and_unsorted_detected() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    dup = pd.concat([full, full.iloc[[0]]])
    rep = _validate(dup, "2024-06-03", "2024-06-04")
    assert {"DUPLICATE_TIMESTAMPS", "NOT_SORTED"} <= rep.codes("error")


def test_off_grid_and_out_of_window_detected() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    extra = pd.Series(
        [1.0, 2.0],
        index=pd.DatetimeIndex(
            [
                full.index[0] + pd.Timedelta(minutes=30),
                full.index[-1] + pd.Timedelta(hours=1),
            ]
        ),
    )
    rep = _validate(pd.concat([full, extra]).sort_index(), "2024-06-03", "2024-06-04")
    off = next(i for i in rep.issues if i.code == "OFF_GRID_TIMESTAMPS")
    assert off.count == 2


def test_resolution_mismatch_detected_both_ways() -> None:
    qh = _complete("2025-10-01", "2025-10-02", "quarterhour")
    rep = _validate(qh, "2025-10-01", "2025-10-02", "hour")
    assert "RESOLUTION_MISMATCH" in rep.codes("error")
    hourly = _complete("2025-10-01", "2025-10-02", "hour")
    rep2 = _validate(hourly, "2025-10-01", "2025-10-02", "quarterhour")
    assert {"RESOLUTION_MISMATCH", "MISSING_INTERVALS"} <= rep2.codes("error")


def test_bounds_scalar_and_time_varying() -> None:
    full = _complete("2024-06-03", "2024-06-04")  # values 0..23
    rep = _validate(full, "2024-06-03", "2024-06-04", lower=1.0, upper=22.0)
    assert next(i for i in rep.issues if i.code == "BELOW_MINIMUM").count == 1
    assert next(i for i in rep.issues if i.code == "ABOVE_MAXIMUM").count == 1
    upper = pd.Series(100.0, index=full.index)
    upper.iloc[10] = 5.0
    rep2 = _validate(full, "2024-06-03", "2024-06-04", upper=upper)
    assert next(i for i in rep2.issues if i.code == "ABOVE_MAXIMUM").count == 1


def test_nan_is_not_a_bound_violation() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    full.iloc[0] = math.nan
    rep = _validate(
        full,
        "2024-06-03",
        "2024-06-04",
        lower=-1e9,
        upper=1e9,
        missing_severity="warning",
    )
    assert rep.ok


def test_non_utc_index_rejected() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    local = full.copy()
    local.index = local.index.tz_convert(TZ)
    rep = _validate(local, "2024-06-03", "2024-06-04")
    assert rep.codes("error") == {"INDEX_NOT_UTC"}


def test_collapsed_duplicates_reported_as_info() -> None:
    rep = _validate(
        _complete("2024-06-03", "2024-06-04"),
        "2024-06-03",
        "2024-06-04",
        duplicates_collapsed=3,
    )
    assert rep.ok and "DUPLICATES_COLLAPSED" in rep.codes("info")


def test_summary_is_readable() -> None:
    full = _complete("2024-06-03", "2024-06-04")
    text = _validate(full.drop(full.index[0]), "2024-06-03", "2024-06-04").summary()
    assert text.startswith("[FAIL]") and "MISSING_INTERVALS" in text
