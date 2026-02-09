# KXBTC15M Data Collector Bot — Implementation Plan

## Purpose
Standalone bot that records per-second market snapshots for all KXBTC15M markets. Produces a JSONL "backtest database" for offline strategy development, parameter tuning, and model validation.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ Kalshi REST  │────▶│              │     │             │
│ (discovery)  │     │   Collector  │────▶│  JSONL file │
│              │     │              │     │             │
│ Kalshi WS    │────▶│              │     └─────────────┘
│ (orderbook)  │     │              │
│              │     │              │
│ Exchange WS  │────▶│              │
│ (BRTI proxy) │     └──────────────┘
└─────────────┘
```

Runs independently from the trading bot. No shared state, no trading logic. Just listens and writes.

## Output Format

One JSONL file per day: `data/kxbtc15m-2026-02-09.jsonl`

### Record Types

**1. `market_open` — Market appears on Kalshi**
```json
{
  "type": "market_open",
  "time": "2026-02-09T20:15:00.123Z",
  "ticker": "KXBTC15M-26FEB091530-30",
  "open_time": "2026-02-09T20:15:00Z",
  "expected_expiration": "2026-02-09T20:35:00Z"
}
```

**2. `strike_set` — Strike becomes available (immediately when market opens)**
```json
{
  "type": "strike_set",
  "time": "2026-02-09T20:15:02.456Z",
  "ticker": "KXBTC15M-26FEB091530-30",
  "strike": 70382.44,
  "elapsed_since_open_secs": 2
}
```

**3. `tick` — Per-second snapshot (the core data)**
```json
{
  "type": "tick",
  "time": "2026-02-09T20:25:15.001Z",
  "ticker": "KXBTC15M-26FEB091530-30",
  "strike": 70382.44,
  "brti": 70395.12,
  "yes_bid": 55,
  "yes_ask": 57,
  "no_bid": 43,
  "no_ask": 45,
  "spread": 2,
  "volume": 1523,
  "open_interest": 890,
  "secs_until_expiry": 595,
  "feeds": {
    "coinbase": 70394.50,
    "kraken": 70395.75,
    "bitstamp": 70396.10
  }
}
```

**4. `settlement` — Market resolution**
```json
{
  "type": "settlement",
  "time": "2026-02-09T20:35:00.789Z",
  "ticker": "KXBTC15M-26FEB091530-30",
  "strike": 70382.44,
  "avg_brti": 70390.23,
  "result": "yes",
  "settlement_ticks": [70388.1, 70389.5, ...]
}
```

## Project Structure

```
cmd/datacollector/main.go      # Entry point
internal/collector/
    collector.go                # Main loop: discover → track → record
    writer.go                   # JSONL writer with daily file rotation
```

Reuse existing packages from the trading bot:
- `internal/kalshi` — API client (auth, REST, market fetching)
- `internal/feed` — Exchange WebSocket feeds (Coinbase, Kraken, Bitstamp)
- `internal/config` — Env var loading (API keys, paths)

## Collector Logic

```
Discovery (adaptive polling):
    Poll every 1s during first 10s of :00/:15/:30/:45 minutes
    Poll every 5s otherwise

    GET /markets?series_ticker=KXBTC15M&status=open
    for each new market:
        emit market_open record
        GET /markets/{ticker} (fetch full details including strike)
        if strike available:
            emit strike_set record
        add to tracked markets

every 1 second:
    for each tracked market:
        if !strike_ready:
            GET /markets/{ticker}
            if strike available:
                emit strike_set record

        snapshot brti proxy
        GET /markets/{ticker}/orderbook (depth=1)
        emit tick record

    for each expired market:
        compute settlement average
        emit settlement record
        remove from tracked
```

## Key Differences from Trading Bot

| Concern | Trading Bot | Data Collector |
|---------|------------|----------------|
| Purpose | Execute trades | Record data |
| Orderbook polling | Only when evaluating edge | Every second for every market |
| Strike handling | Needed for trading decisions | Recorded as data point |
| Output | journal-{mode}.jsonl | data/kxbtc15m-{date}.jsonl |
| Modes | sniper/full/scalp | Single mode, always on |
| Risk | Places orders (dry-run or real) | Read-only, never trades |

## CLI Interface

```bash
# Start collecting
./datacollector

# With options
./datacollector --output=./data --series=KXBTC15M

# Environment variables (shared with trading bot via .env)
KALSHI_API_KEY_ID=...
KALSHI_PRIV_KEY_PATH=./kalshi_private_key.pem
KALSHI_ENV=prod
DATA_OUTPUT_DIR=./data
```

## Rate Limit Budget

Per market per second:
- 1 orderbook fetch (GET /markets/{ticker}/orderbook)

With 1 active market: ~1 read/sec (well under 20/sec limit)
With 3 concurrent markets: ~3 reads/sec (still fine)

Discovery polling: 1 read/sec during market open windows (10s), 1 read every 5s otherwise.
Total budget: ~4-5 reads/sec sustained, well under 20/sec limit.

## Daily File Rotation

Writer rotates files at midnight UTC:
- `data/kxbtc15m-2026-02-09.jsonl`
- `data/kxbtc15m-2026-02-10.jsonl`
- etc.

Each file is self-contained. No cross-file dependencies.

## Storage Estimate

Per tick: ~300 bytes
Per market (15 min = 900 ticks): ~270 KB
Per day (assuming markets run 8 hours = 32 markets): ~8.6 MB
Per month: ~260 MB

Easily manageable as flat files. Can compress old days with gzip.

## botctl Integration

Add to botctl:
```bash
./botctl start-collector    # Start data collector
./botctl stop-collector     # Stop data collector
./botctl collector-status   # Show stats (markets tracked, ticks recorded today)
```

Uses same PID/log pattern: `.collector.pid`, `collector.log`

## Analysis Scripts (future)

Once data is collected, build analysis tools:

```bash
# Count markets per day
jq -s '[.[] | select(.type=="settlement")] | length' data/kxbtc15m-2026-02-09.jsonl

# Win rate if you always bought YES at market open
jq -s '[.[] | select(.type=="settlement")] | {
  total: length,
  yes: [.[] | select(.result=="yes")] | length
}' data/kxbtc15m-2026-02-09.jsonl

# Average spread over time
jq -s '[.[] | select(.type=="tick") | .spread] | add / length' data/kxbtc15m-2026-02-09.jsonl

# Backtest: what if we bought YES whenever trueProb > market price?
# (requires building a Python/Go script that replays ticks through the model)
```

## Build Sequence

1. Create `cmd/datacollector/main.go` — arg parsing, signal handling, init feeds + client
2. Create `internal/collector/writer.go` — daily-rotating JSONL writer
3. Create `internal/collector/collector.go` — market discovery, tick loop, settlement detection
4. Add `start-collector` / `stop-collector` to botctl
5. Test: run for 1 hour, verify JSONL output with `jq`
6. Let it run 24/7 alongside the trading bot
