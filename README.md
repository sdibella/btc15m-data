# KXBTC15M Data Collector

Per-second price data collector for backtesting Kalshi 15-minute Bitcoin markets.

Records:
- **BRTI** (median of Binance, Coinbase, Kraken, Bitstamp)
- **Individual exchange prices** from 4 WebSocket feeds
- **Kalshi market snapshots** (bid/ask/last/volume/strike/time remaining)

## Quick Start

### Setup
```bash
cp .env.example .env
# Edit .env with KALSHI_API_KEY_ID and KALSHI_PRIV_KEY_PATH
go build ./cmd/datacollector
```

### Run
```bash
./botctl start          # Start collector
./botctl status         # Check status + stats
./botctl logs           # Watch logs
./botctl stop           # Graceful shutdown
```

### Data Output
JSONL files in `./data/`:
```bash
jq '.' data/kxbtc15m-*.jsonl | head -50
```

Each line:
```json
{
  "type": "tick",
  "ts": "2026-02-09T23:46:46.459114Z",
  "brti": 70241.3275,
  "coinbase": 70241.155,
  "kraken": 70244.25,
  "bitstamp": 70241.5,
  "binance": 70241.1,
  "markets": [
    {
      "ticker": "KXBTC15M-26FEB091900-00",
      "yes_bid": 48,
      "yes_ask": 51,
      "last_price": 48,
      "volume": 871,
      "open_interest": 563,
      "strike": 70353.48,
      "secs_left": 1093
    }
  ]
}
```

## Environment

`.env`:
```
KALSHI_API_KEY_ID=<your-api-key>
KALSHI_PRIV_KEY_PATH=./kalshi_private_key.pem
KALSHI_ENV=prod
OUTPUT_DIR=./data
SERIES_TICKER=KXBTC15M
```

## Architecture

- `cmd/datacollector/` — Entry point (flags, graceful shutdown)
- `internal/config/` — Config loading from .env
- `internal/kalshi/` — Kalshi API client (auth + GetMarkets)
- `internal/feed/` — 4 exchange WebSocket feeds (Binance, Coinbase, Kraken, Bitstamp)
- `internal/collector/` — Per-second tick writer + JSONL daily rotation
- `botctl` — Process management (start/stop/status/logs)

## Development

Build:
```bash
go build -o datacollector ./cmd/datacollector
```

Test run (30s):
```bash
./datacollector --debug &
PID=$!
sleep 30
kill $PID
```

Check data:
```bash
wc -l data/kxbtc15m-*.jsonl
head -1 data/kxbtc15m-*.jsonl | jq .
```

## Notes

- Daily rotation: new JSONL file at midnight UTC
- Thread-safe writes via mutex in writer
- Feeds auto-reconnect on disconnect
- 1 API request/sec to Kalshi (sustainable for indefinite collection)
