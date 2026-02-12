#!/usr/bin/env python3
"""Convert JSONL tick data to Parquet format.

Usage:
    python3 tools/jsonl2parquet.py              # convert all unconverted
    python3 tools/jsonl2parquet.py 2026-02-10   # specific date
    python3 tools/jsonl2parquet.py --force       # re-convert existing
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcdata.loader import DATA_DIR, PARQUET_DIR, PREFIX, available_dates, load_day


def convert_date(date: str, force: bool = False) -> bool:
    """Convert a single date to parquet. Returns True if converted."""
    out = PARQUET_DIR / f"{PREFIX}-{date}.parquet"
    if out.exists() and not force:
        return False

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    df = load_day(date, prefer_parquet=False)
    if df.empty:
        print(f"  {date}: no data, skipping")
        return False

    df.to_parquet(out, index=False, engine="pyarrow")
    print(f"  {date}: {len(df):,} rows -> {out.name}")
    return True


def main():
    force = "--force" in sys.argv
    dates = [a for a in sys.argv[1:] if not a.startswith("-")]

    if not dates:
        dates = available_dates()

    if not dates:
        print("No data files found.")
        return

    converted = 0
    for d in dates:
        try:
            if convert_date(d, force=force):
                converted += 1
        except FileNotFoundError:
            print(f"  {d}: not found, skipping")
        except Exception as e:
            print(f"  {d}: error: {e}")

    print(f"\nConverted {converted}/{len(dates)} files.")


if __name__ == "__main__":
    main()
