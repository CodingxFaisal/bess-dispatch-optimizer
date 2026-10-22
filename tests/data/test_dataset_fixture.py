"""End-to-end offline assembly from the committed SMARD fixture (real data).

Fixture: Bundesnetzagentur | SMARD.de, CC BY 4.0 (see tests/fixtures/README.md).
"""

import datetime as dt
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from gridflex.config import GridFlexConfig
from gridflex.data import (
    ATTRIBUTION,
    DataUnavailableError,
    DataValidationError,
    MarketRegimeError,
    SmardClient,
    build_market_dataset,
)
from gridflex.data.cache import sha256_hex
from tests_helpers import FIXTURE_CACHE, ForbiddenSession

SERIES = ("price", "load_forecast")


def _client(gf_cfg: GridFlexConfig, root: Path = FIXTURE_CACHE) -> SmardClient:
    return SmardClient.from_config(gf_cfg.data, cache_dir=root, offline=True)


def _build(gf_cfg: GridFlexConfig, start: dt.date, end: dt.date, **kw):
    return build_market_dataset(
        gf_cfg,
        _client(gf_cfg, kw.pop("root", FIXTURE_CACHE)),
        start,
        end,
        series=kw.pop("series", SERIES),
        **kw,
    )


def test_spring_dst_window(gf_cfg: GridFlexConfig) -> None:
    ds = _build(gf_cfg, dt.date(2024, 3, 30), dt.date(2024, 4, 2))
    assert ds.ok and ds.resolution == "hour"
    assert len(ds.frame) == 24 + 23 + 24
    local = ds.local_frame()
    day = local[local.index.date == dt.date(2024, 3, 31)]
    assert len(day) == 23
    assert 2 not in set(day.index.hour)  # 02:00 local does not exist
    assert "DST_DAYS" in ds.reports["price"].codes("info")
    assert str(ds.frame.index.tz) == "UTC"


def test_autumn_dst_window_has_repeated_local_hour(gf_cfg: GridFlexConfig) -> None:
    ds = _build(gf_cfg, dt.date(2024, 10, 26), dt.date(2024, 10, 29))
    assert ds.ok and len(ds.frame) == 24 + 25 + 24
    local = ds.local_frame()
    day = local[local.index.date == dt.date(2024, 10, 27)]
    assert len(day) == 25
    assert (day.index.hour == 2).sum() == 2  # 02:00 CEST and 02:00 CET
    assert ds.frame.index.is_unique  # UTC identity stays unique


def test_quarterhour_regime_and_unit_conversion(gf_cfg: GridFlexConfig) -> None:
    ds = _build(gf_cfg, dt.date(2025, 10, 1), dt.date(2025, 10, 4))
    assert ds.ok and ds.resolution == "quarterhour" and ds.interval_hours == 0.25
    assert len(ds.frame) == 3 * 96
    # Read the raw payload independently and check MWh-per-15-min x 4 = MW.
    chunk = next((FIXTURE_CACHE / "smard/DE/411/quarterhour").glob("chunk_*[0-9].json"))
    raw = dict((int(t), v) for t, v in json.loads(chunk.read_bytes())["series"])
    t0 = ds.frame.index[0]
    assert ds.frame.loc[t0, "load_forecast"] == pytest.approx(
        4 * raw[int(t0.timestamp() * 1000)]
    )
    # 15-minute prices really differ within an hour after the switch.
    first_hour = ds.frame["price"].iloc[:4]
    assert first_hour.nunique() > 1


def test_last_hourly_days_before_switch(gf_cfg: GridFlexConfig) -> None:
    ds = _build(gf_cfg, dt.date(2025, 9, 29), dt.date(2025, 10, 1))
    assert ds.ok and ds.resolution == "hour" and len(ds.frame) == 48


def test_regime_mismatch_rejected(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(MarketRegimeError, match="Split the window"):
        _build(gf_cfg, dt.date(2025, 9, 30), dt.date(2025, 10, 2))
    with pytest.raises(MarketRegimeError, match="hour MTU regime"):
        _build(
            gf_cfg, dt.date(2025, 9, 29), dt.date(2025, 10, 1), resolution="quarterhour"
        )
    with pytest.raises(MarketRegimeError, match="quarterhour MTU regime"):
        _build(gf_cfg, dt.date(2025, 10, 1), dt.date(2025, 10, 2), resolution="hour")


def test_window_not_in_fixture_is_unavailable(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(DataUnavailableError, match="offline mode"):
        _build(gf_cfg, dt.date(2024, 6, 1), dt.date(2024, 6, 2))


def test_unknown_series(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(KeyError, match="unknown series"):
        _build(gf_cfg, dt.date(2024, 3, 30), dt.date(2024, 4, 2), series=("gas_price",))


def test_offline_never_touches_network(gf_cfg: GridFlexConfig) -> None:
    client = SmardClient.from_config(
        gf_cfg.data, cache_dir=FIXTURE_CACHE, offline=True, session=ForbiddenSession()
    )
    build_market_dataset(gf_cfg, client, dt.date(2024, 3, 30), dt.date(2024, 4, 2))


def test_provenance_is_serialisable_and_attributed(gf_cfg: GridFlexConfig) -> None:
    ds = _build(gf_cfg, dt.date(2024, 3, 30), dt.date(2024, 4, 2))
    prov = json.loads(json.dumps(ds.provenance()))
    assert prov["timestamp_convention"] == "UTC interval start"
    price = prov["series"]["price"]
    assert price["attribution"] == ATTRIBUTION and price["license"] == "CC BY 4.0"
    assert price["smard_id"] == 4169 and price["n_chunks"] == 2
    assert price["availability_rule"] == "D-1 13:00 Europe/Berlin"
    assert prov["validation"]["price"]["ok"] is True


def _corrupt_copy(tmp_path: Path, null_at_ms: int) -> Path:
    """Copy the fixture and null one price point, re-hashing the sidecar."""
    root = tmp_path / "cache"
    shutil.copytree(FIXTURE_CACHE, root)
    for chunk in (root / "smard/DE/4169/hour").glob("chunk_*[0-9].json"):
        body = json.loads(chunk.read_bytes())
        stamps = [p[0] for p in body["series"]]
        if null_at_ms in stamps:
            body["series"][stamps.index(null_at_ms)][1] = None
            content = json.dumps(body).encode()
            chunk.write_bytes(content)
            meta_path = chunk.with_name(chunk.stem + ".meta.json")
            meta = json.loads(meta_path.read_text())
            meta["sha256"] = sha256_hex(content)
            meta_path.write_text(json.dumps(meta))
            return root
    raise AssertionError("timestamp not in fixture")


def test_missing_price_fails_strict_and_is_never_filled(
    gf_cfg: GridFlexConfig, tmp_path: Path
) -> None:
    gap = pd.Timestamp("2024-03-31 10:00", tz="UTC")
    root = _corrupt_copy(tmp_path, int(gap.timestamp() * 1000))
    with pytest.raises(DataValidationError, match="NULL_VALUES"):
        _build(gf_cfg, dt.date(2024, 3, 30), dt.date(2024, 4, 2), root=root)
    ds = _build(
        gf_cfg, dt.date(2024, 3, 30), dt.date(2024, 4, 2), root=root, strict=False
    )
    assert not ds.ok
    assert pd.isna(ds.frame.loc[gap, "price"])  # no interpolation, no zero
    assert ds.frame["price"].isna().sum() == 1
    assert "INCOMPLETE_DAYS" in ds.reports["price"].codes("error")
