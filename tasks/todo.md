# KXBTC15M Data Collector — Implementation

## Build Sequence
- [x] `go.mod` + `go.sum` + `.env` — module foundation
- [x] `internal/config/config.go` — simplified config (no trading fields)
- [x] `internal/kalshi/auth.go` — RSA auth (copied from KalshiCrypto)
- [x] `internal/kalshi/client.go` — stripped client (read-only: GetMarkets, GetMarket, GetOrderbook, GetBalance)
- [x] `internal/feed/` — all 5 files copied with updated import paths
- [x] `internal/collector/writer.go` — daily-rotating JSONL writer
- [x] `internal/collector/collector.go` — discovery loop + tick loop + settlement detection
- [x] `cmd/datacollector/main.go` — entry point with flags, feeds, graceful shutdown
- [x] `botctl` — process management script
- [x] `go build ./cmd/datacollector` — compiles cleanly

## Verification
- [x] Compiles cleanly
- [ ] Run for ~30 seconds — verify auth, feeds connect, data written
- [ ] Check `data/kxbtc15m-*.jsonl` for valid JSON records
- [ ] `./botctl start && ./botctl status && ./botctl stop` — process management works

## Review
- All code compiles. No trading methods included — collector is read-only.
- Feed package copied as-is from KalshiCrypto with import path changes only.
- Kalshi client stripped to: GetMarkets, GetMarket, GetOrderbook, GetBalance (no CreateOrder, CancelOrder, GetPositions).
- Config simplified: only KalshiAPIKeyID, KalshiPrivKeyPath, KalshiEnv, OutputDir, SeriesTicker.
