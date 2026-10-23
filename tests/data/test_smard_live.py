"""Live contract checks against smard.de. Deselected by default.

    uv run pytest -m network

They verify the assumptions the adapter is built on: endpoint shapes, both
resolutions, and that energy series are MWh per interval (four quarter-hour
values sum to the hourly value) while prices are not.
"""

import datetime as dt
from pathlib import Path

import pytest

from gridflex.config import GridFlexConfig
from gridflex.data import SmardClient
from gridflex.data.timeutils import local_window_utc

pytestmark = pytest.mark.network


def _fetch(gf_cfg: GridFlexConfig, tmp_path: Path, sid: int, res: str):
    client = SmardClient.from_config(gf_cfg.data, cache_dir=tmp_path, offline=False)
    s, e = local_window_utc(dt.date(2024, 6, 3), dt.date(2024, 6, 4), "Europe/Berlin")
    return client.fetch(sid, res, s, e).values  # type: ignore[arg-type]


@pytest.mark.parametrize("sid", [411, 125, 123, 3791])
def test_energy_series_are_mwh_per_interval(
    gf_cfg: GridFlexConfig, tmp_path: Path, sid: int
) -> None:
    hourly = _fetch(gf_cfg, tmp_path, sid, "hour")
    quarter = _fetch(gf_cfg, tmp_path, sid, "quarterhour")
    summed = quarter.resample("1h").sum(min_count=4)
    assert len(hourly) == 24 and len(quarter) == 96
    assert (summed - hourly).abs().max() < 1e-6 * hourly.abs().max()


def test_price_quarterhours_repeat_hourly_before_switch(
    gf_cfg: GridFlexConfig, tmp_path: Path
) -> None:
    hourly = _fetch(gf_cfg, tmp_path, 4169, "hour")
    quarter = _fetch(gf_cfg, tmp_path, 4169, "quarterhour")
    assert (quarter.resample("1h").mean() - hourly).abs().max() < 1e-9
    assert (quarter.resample("1h").nunique() == 1).all()
