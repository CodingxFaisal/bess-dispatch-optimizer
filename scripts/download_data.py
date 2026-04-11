"""Download and cache the SMARD dataset for a date range.

    python scripts/download_data.py --start 2022-01-01 --end 2025-01-01

Data is cached under data/raw (per series) and data/processed (assembled), so
re-runs and the analysis scripts hit the network at most once.
"""

from __future__ import annotations

import argparse

from _common import load_or_build_dataset


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument("--refresh", action="store_true", help="ignore processed cache")
    args = ap.parse_args()

    print(f"Downloading SMARD data {args.start} -> {args.end} ...")
    df = load_or_build_dataset(args.start, args.end, refresh=args.refresh)
    print(f"Rows: {len(df):,}")
    print(f"Columns: {list(df.columns)}")
    print(f"Price range EUR/MWh: [{df['price'].min():.1f}, {df['price'].max():.1f}], "
          f"mean {df['price'].mean():.1f}")
    neg = int((df["price"] < 0).sum())
    print(f"Negative-price hours: {neg} ({neg / len(df) * 100:.1f}%)")


if __name__ == "__main__":
    main()
