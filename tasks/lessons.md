# Lessons Learned

## 2026-02-10: secs_left counts to SETTLEMENT, not market close

**Problem**: Backtests used `secs_left <= 300` for "5 min before expiry" which was actually at market close time, producing 100¢ prices that looked like all markets were decided.

**Root Cause**: `secs_left` counts down to the Kalshi expiration/settlement time, which is ~294 seconds (≈5 min) AFTER the market closes for trading.

**Correct Mapping**:
```
Market timeline: OPEN → CLOSE → SETTLE (~5 min later)

secs_left ≈ 294  →  Market CLOSES (trading stops)
secs_left ≈ 594  →  5 min before close (real trading)
secs_left ≈ 894  →  10 min before close (real trading)

Formula: secs_left_target = SETTLEMENT_DELAY + (minutes_before_close * 60)
         where SETTLEMENT_DELAY ≈ 294
```

**Rule**: Always use `secs_left > 294` to filter for active trading prices. Anything at or below 294 is post-close data with stale 99-100¢ prices.

## 2026-02-10: Post-close prices are false (99-100¢)

When markets change status from "active" to "closed"/"determined", Kalshi reports 99-100¢ bid/ask. These are NOT tradeable prices. Always filter by:
1. `status == "active"` (for data with status field), OR
2. `secs_left > 294` (universal fallback)
