"""Unified data loader for BTC 15-min Kalshi tick data.

Supports parquet, gzipped JSONL, and plain JSONL with automatic format selection.
"""

import gzip
import json
import os
from glob import glob
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PARQUET_DIR = DATA_DIR / "parquet"
PREFIX = "kxbtc15m"


def available_dates() -> list[str]:
    """Return sorted list of available date strings (YYYY-MM-DD)."""
    dates = set()

    # Check parquet
    for f in glob(str(PARQUET_DIR / f"{PREFIX}-*.parquet")):
        d = Path(f).stem.replace(f"{PREFIX}-", "")
        dates.add(d)

    # Check gzipped JSONL
    for f in glob(str(DATA_DIR / f"{PREFIX}-*.jsonl.gz")):
        d = Path(f).name.replace(f"{PREFIX}-", "").replace(".jsonl.gz", "")
        dates.add(d)

    # Check plain JSONL
    for f in glob(str(DATA_DIR / f"{PREFIX}-*.jsonl")):
        d = Path(f).name.replace(f"{PREFIX}-", "").replace(".jsonl", "")
        dates.add(d)

    return sorted(dates)


def load_day(date: str, prefer_parquet: bool = True) -> pd.DataFrame:
    """Load one day of tick data, returning a flattened DataFrame.

    Format preference: parquet > .jsonl.gz > .jsonl
    """
    if prefer_parquet:
        pq = PARQUET_DIR / f"{PREFIX}-{date}.parquet"
        if pq.exists():
            return pd.read_parquet(pq)

    gz = DATA_DIR / f"{PREFIX}-{date}.jsonl.gz"
    if gz.exists():
        return _load_jsonl(gz, compressed=True)

    plain = DATA_DIR / f"{PREFIX}-{date}.jsonl"
    if plain.exists():
        return _load_jsonl(plain, compressed=False)

    raise FileNotFoundError(f"No data found for {date}")


def load_days(dates: list[str], prefer_parquet: bool = True) -> pd.DataFrame:
    """Load multiple days and concatenate."""
    frames = [load_day(d, prefer_parquet=prefer_parquet) for d in dates]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_all(prefer_parquet: bool = True) -> pd.DataFrame:
    """Load all available data."""
    return load_days(available_dates(), prefer_parquet=prefer_parquet)


def _load_jsonl(path: Path, compressed: bool) -> pd.DataFrame:
    """Parse a JSONL (or .jsonl.gz) file into a flattened DataFrame."""
    rows = []
    opener = gzip.open if compressed else open
    with opener(path, "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tick = json.loads(line)
            if tick.get("type") != "tick":
                continue
            base = {
                "ts": tick["ts"],
                "brti": tick.get("brti", 0.0),
                "coinbase": tick.get("coinbase", 0.0),
                "kraken": tick.get("kraken", 0.0),
                "bitstamp": tick.get("bitstamp", 0.0),
            }
            for mkt in tick.get("markets", []):
                row = {
                    **base,
                    "ticker": mkt.get("ticker", ""),
                    "yes_bid": mkt.get("yes_bid", 0),
                    "yes_ask": mkt.get("yes_ask", 0),
                    "last_price": mkt.get("last_price", 0),
                    "volume": mkt.get("volume", 0),
                    "open_interest": mkt.get("open_interest", 0),
                    "strike": mkt.get("strike", 0.0),
                    "secs_left": mkt.get("secs_left", 0),
                    "status": mkt.get("status", ""),
                    "result": mkt.get("result", ""),
                    "yes_book": json.dumps(mkt["yes_book"]) if "yes_book" in mkt else "",
                    "no_book": json.dumps(mkt["no_book"]) if "no_book" in mkt else "",
                }
                rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
        for col in ("yes_bid", "yes_ask", "last_price", "volume", "open_interest", "secs_left"):
            df[col] = df[col].astype("int32")
    return df
