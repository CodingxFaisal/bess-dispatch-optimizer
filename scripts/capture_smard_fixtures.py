"""Download the small SMARD extract committed as the offline test fixture.

    uv run python scripts/capture_smard_fixtures.py [--out tests/fixtures/smard_cache]

The fixture is a raw cache (payloads byte-for-byte as served, plus provenance
sidecars) produced by the production client, so offline tests exercise the
same code path as live runs. It covers:

* hourly regime: the 2024 spring (31 Mar, 23 h) and autumn (27 Oct, 25 h) DST
  transitions, and the last hourly days before the 15-minute switch;
* quarter-hour regime: the first days after the switch (from 1 Oct 2025);

for the day-ahead price (4169) and load forecast (411).

Data: Bundesnetzagentur | SMARD.de, licensed CC BY 4.0.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from _common import CONFIG, REPO_ROOT
from gridflex.config import load_config
from gridflex.data import SmardClient
from gridflex.data.timeutils import local_window_utc

WINDOWS = [
    ("hour", dt.date(2024, 3, 30), dt.date(2024, 4, 2)),
    ("hour", dt.date(2024, 10, 26), dt.date(2024, 10, 29)),
    ("hour", dt.date(2025, 9, 29), dt.date(2025, 10, 1)),
    ("quarterhour", dt.date(2025, 10, 1), dt.date(2025, 10, 4)),
]
SERIES = ("price", "load_forecast")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", default=str(REPO_ROOT / "tests" / "fixtures" / "smard_cache")
    )
    args = ap.parse_args()

    cfg = load_config(CONFIG)
    client = SmardClient.from_config(cfg.data, cache_dir=Path(args.out), offline=False)
    for resolution, start, end in WINDOWS:
        start_utc, end_utc = local_window_utc(start, end, cfg.market.timezone)
        for key in SERIES:
            sid = cfg.data.series[key].smard_id
            raw = client.fetch(sid, resolution, start_utc, end_utc, refresh=True)  # type: ignore[arg-type]
            print(
                f"{key:14s} {sid:>5} {resolution:11s} {start} -> {end}: "
                f"{len(raw.values)} points, {len(raw.chunks)} chunk(s)"
            )


if __name__ == "__main__":
    main()
