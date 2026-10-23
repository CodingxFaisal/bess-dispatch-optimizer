"""Exceptions raised by the data layer. All derive from :class:`GridFlexDataError`."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gridflex.data.validate import ValidationReport


class GridFlexDataError(Exception):
    """Base class for data acquisition, integrity and validation errors."""


class SourceUnavailableError(GridFlexDataError):
    """The remote source could not be reached or refused the request."""


class SourceNotFoundError(SourceUnavailableError):
    """The remote source answered 404 (unknown series, resolution or chunk)."""


class SourceResponseError(GridFlexDataError):
    """The remote source answered, but the payload is malformed."""


class DataUnavailableError(GridFlexDataError):
    """Requested data are not available (offline cache miss, outside coverage)."""


class DataIntegrityError(GridFlexDataError):
    """Data contradict themselves (conflicting duplicates, corrupt cache files)."""


class MarketRegimeError(GridFlexDataError):
    """A request mixes or mismatches day-ahead market time-unit regimes."""


class InformationLeakageError(GridFlexDataError):
    """Data would be used before they were publicly available."""


class DataValidationError(GridFlexDataError):
    """One or more validation reports contain errors."""

    def __init__(self, reports: list[ValidationReport]) -> None:
        self.reports = reports
        lines = [r.summary() for r in reports if not r.ok]
        super().__init__("data validation failed:\n" + "\n".join(lines))
