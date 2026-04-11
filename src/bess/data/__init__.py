"""Data acquisition (SMARD.de -- German regulator, free, no API key)."""

from bess.data.smard import build_dataset, fetch_series

__all__ = ["build_dataset", "fetch_series"]
