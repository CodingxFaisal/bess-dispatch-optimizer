"""Market data retrieval, raw caching, cleaning, validation and availability.

Source: SMARD.de chart data (Bundesnetzagentur | SMARD.de), licensed CC BY 4.0.
See ``docs/DATA_DICTIONARY.md``.
"""

from gridflex.data.availability import (
    assert_available,
    available_from,
    day_ahead_usable_series,
    decision_time,
    latest_available_delivery_day,
)
from gridflex.data.cache import CacheEntryMeta, RawCache
from gridflex.data.clean import CleanSeries, clean_series
from gridflex.data.dataset import (
    ATTRIBUTION,
    LICENSE,
    MarketDataset,
    SeriesMetadata,
    build_market_dataset,
    check_regime,
)
from gridflex.data.errors import (
    DataIntegrityError,
    DataUnavailableError,
    DataValidationError,
    GridFlexDataError,
    InformationLeakageError,
    MarketRegimeError,
    SourceNotFoundError,
    SourceResponseError,
    SourceUnavailableError,
)
from gridflex.data.http import HttpClient
from gridflex.data.smard import RawSeries, SmardClient
from gridflex.data.validate import ValidationIssue, ValidationReport, validate_series

__all__ = [
    "ATTRIBUTION",
    "LICENSE",
    "CacheEntryMeta",
    "CleanSeries",
    "DataIntegrityError",
    "DataUnavailableError",
    "DataValidationError",
    "GridFlexDataError",
    "HttpClient",
    "InformationLeakageError",
    "MarketDataset",
    "MarketRegimeError",
    "RawCache",
    "RawSeries",
    "SeriesMetadata",
    "SmardClient",
    "SourceNotFoundError",
    "SourceResponseError",
    "SourceUnavailableError",
    "ValidationIssue",
    "ValidationReport",
    "assert_available",
    "available_from",
    "build_market_dataset",
    "check_regime",
    "clean_series",
    "day_ahead_usable_series",
    "decision_time",
    "latest_available_delivery_day",
    "validate_series",
]
