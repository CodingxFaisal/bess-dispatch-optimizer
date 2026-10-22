"""Download and cache the SMARD dataset for a date range.

    uv run python scripts/download_data.py --start 2022-01-01 --end 2025-01-01

Raw SMARD payloads and provenance sidecars are cached under data/cache (see
gridflex.data.cache for the invalidation policy). The assembled dataset is
validated; any price gap, duplicate or out-of-range value aborts the run.
"""

from __future__ import annotations

import argparse

from _common import load_cfg, load_dataset


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="re-download every chunk, ignoring the cache policy",
    )
    ap.add_argument("--offline", action="store_true", help="use the cache only")
    args = ap.parse_args()

    print(f"Loading SMARD data for delivery days {args.start} -> {args.end} ...")
    ds = load_dataset(
        load_cfg(),
        args.start,
        args.end,
        offline=args.offline or None,
        refresh=args.refresh,
    )
    for report in ds.reports.values():
        print(report.summary())
    df = ds.frame
    print(f"Rows: {len(df):,}")
    print(f"Columns: {list(df.columns)}")
    print(
        f"Price range EUR/MWh: [{df['price'].min():.1f}, {df['price'].max():.1f}], "
        f"mean {df['price'].mean():.1f}"
    )
    neg = int((df["price"] < 0).sum())
    print(
        f"Negative-price intervals: {neg} ({neg / len(df) * 100:.1f}%), "
        f"resolution {ds.resolution}"
    )


if __name__ == "__main__":
    main()
