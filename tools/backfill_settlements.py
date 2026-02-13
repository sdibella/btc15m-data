#!/usr/bin/env python3
"""Backfill missing settlement results in JSONL data files.

Scans data files for markets that have status but no result,
fetches the result from the Kalshi API, and patches the files.

Usage:
    python3 tools/backfill_settlements.py              # scan all, dry-run
    python3 tools/backfill_settlements.py --apply       # actually patch
    python3 tools/backfill_settlements.py 2026-02-12   # specific date
"""

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PREFIX = "kxbtc15m"


def find_missing(path, compressed):
    """Find markets seen in a file that have no result."""
    opener = gzip.open if compressed else open
    seen = {}  # ticker -> {has_result, last_status}
    with opener(path, "rt") as f:
        for line in f:
            d = json.loads(line.strip())
            if d.get("type") != "tick":
                continue
            for m in d.get("markets", []):
                t = m["ticker"]
                r = m.get("result", "")
                if t not in seen:
                    seen[t] = {"has_result": False, "last_status": ""}
                seen[t]["last_status"] = m.get("status", "")
                if r:
                    seen[t]["has_result"] = True

    missing = [t for t, v in seen.items() if not v["has_result"]]
    return sorted(missing), len(seen)


def fetch_result(ticker):
    """Fetch market result from Kalshi API via the Go client."""
    # Use a quick Go program to call GetMarket
    code = f'''package main
import (
    "context"
    "fmt"
    "github.com/gw/btc15m-data/internal/config"
    "github.com/gw/btc15m-data/internal/kalshi"
)
func main() {{
    cfg, _ := config.Load()
    c, _ := kalshi.NewClient(cfg)
    m, err := c.GetMarket(context.Background(), "{ticker}")
    if err != nil {{ fmt.Println("ERROR:", err); return }}
    fmt.Printf("%s|%s\\n", m.Status, m.Result)
}}'''
    # Actually, let's just use the existing retrofit binary or curl.
    # Simpler: shell out to Go
    result = subprocess.run(
        ["go", "run", "-exec", "", "-"],
        input=code, capture_output=True, text=True, timeout=30,
    )
    # This won't work easily. Let's use a different approach.
    return None


def fetch_results_go(tickers):
    """Fetch multiple market results using a temporary Go program."""
    if not tickers:
        return {}

    ticker_list = ", ".join(f'"{t}"' for t in tickers)
    prog = f'''package main

import (
    "context"
    "fmt"
    "github.com/gw/btc15m-data/internal/config"
    "github.com/gw/btc15m-data/internal/kalshi"
    "time"
)

func main() {{
    cfg, err := config.Load()
    if err != nil {{ fmt.Println("CONFIG_ERROR:", err); return }}
    c, err := kalshi.NewClient(cfg)
    if err != nil {{ fmt.Println("CLIENT_ERROR:", err); return }}
    ctx := context.Background()
    tickers := []string{{{ticker_list}}}
    for _, t := range tickers {{
        m, err := c.GetMarket(ctx, t)
        if err != nil {{ fmt.Printf("%s|ERROR|%v\\n", t, err); continue }}
        fmt.Printf("%s|%s|%s\\n", t, m.Status, m.Result)
        time.Sleep(500 * time.Millisecond)
    }}
}}'''

    # Write temp Go file
    tmp = Path(DATA_DIR).parent / "cmd" / "_backfill_tmp" / "main.go"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(prog)

    try:
        result = subprocess.run(
            ["go", "run", str(tmp)],
            capture_output=True, text=True, timeout=60,
            cwd=str(DATA_DIR.parent),
        )
        results = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3 and parts[1] != "ERROR":
                results[parts[0]] = parts[2]  # ticker -> result
        return results
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def patch_file(path, compressed, patches):
    """Patch a data file with settlement results."""
    opener = gzip.open if compressed else open
    lines = []
    patched = 0

    with opener(path, "rt") as f:
        for line in f:
            d = json.loads(line.strip())
            if d.get("type") == "tick":
                for m in d.get("markets", []):
                    if m["ticker"] in patches and not m.get("result"):
                        m["result"] = patches[m["ticker"]]
                        if m.get("status") in ("active", ""):
                            m["status"] = "finalized"
                        patched += 1
            lines.append(json.dumps(d, separators=(",", ":")))

    # Write to temp, then rename
    suffix = ".gz" if compressed else ""
    tmp_path = str(path) + ".backfill.tmp" + suffix
    write_opener = gzip.open if compressed else open
    with write_opener(tmp_path, "wt") as f:
        for line in lines:
            f.write(line + "\n")

    os.rename(tmp_path, path)
    return patched


def main():
    apply = "--apply" in sys.argv
    dates = [a for a in sys.argv[1:] if not a.startswith("-")]

    # Find all data files
    files = []
    if dates:
        for d in dates:
            gz = DATA_DIR / f"{PREFIX}-{d}.jsonl.gz"
            plain = DATA_DIR / f"{PREFIX}-{d}.jsonl"
            if gz.exists():
                files.append((gz, True, d))
            elif plain.exists():
                files.append((plain, False, d))
            else:
                print(f"  {d}: not found")
    else:
        for f in sorted(glob(str(DATA_DIR / f"{PREFIX}-*.jsonl.gz"))):
            d = Path(f).name.replace(f"{PREFIX}-", "").replace(".jsonl.gz", "")
            files.append((Path(f), True, d))
        for f in sorted(glob(str(DATA_DIR / f"{PREFIX}-*.jsonl"))):
            d = Path(f).name.replace(f"{PREFIX}-", "").replace(".jsonl", "")
            # Skip if gz version exists
            if (DATA_DIR / f"{PREFIX}-{d}.jsonl.gz").exists():
                continue
            files.append((Path(f), False, d))

    # Scan for missing results
    all_missing = []
    for path, compressed, date in files:
        missing, total = find_missing(path, compressed)
        if missing:
            print(f"  {date}: {len(missing)}/{total} markets missing result: {missing}")
            all_missing.extend((path, compressed, t) for t in missing)
        else:
            print(f"  {date}: {total} markets, all have results")

    if not all_missing:
        print("\nNo missing settlements.")
        return

    # Fetch from API
    unique_tickers = sorted(set(t for _, _, t in all_missing))
    print(f"\nFetching {len(unique_tickers)} markets from Kalshi API...")
    results = fetch_results_go(unique_tickers)

    for t in unique_tickers:
        r = results.get(t, "NOT FOUND")
        print(f"  {t}: {r}")

    if not apply:
        print("\nDry run. Use --apply to patch files.")
        return

    # Patch files
    patches_by_file = {}
    for path, compressed, ticker in all_missing:
        if ticker in results:
            key = (str(path), compressed)
            if key not in patches_by_file:
                patches_by_file[key] = {}
            patches_by_file[key][ticker] = results[ticker]

    for (path_str, compressed), patches in patches_by_file.items():
        count = patch_file(Path(path_str), compressed, patches)
        print(f"  Patched {path_str}: {count} snapshots updated")

    print("\nDone.")


if __name__ == "__main__":
    main()
