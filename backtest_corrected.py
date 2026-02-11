#!/usr/bin/env python3
"""
Backtest: Buy ≥68¢ contracts at N minutes before market CLOSE.

CORRECTED TIMING:
  secs_left counts down to SETTLEMENT, not to market close.
  Markets close ~294 seconds before settlement.
  So: secs_left_at_close ≈ 294
      5 min before close  ≈ secs_left 594
      10 min before close ≈ secs_left 894

Usage: python3 backtest_corrected.py [--mins-before-close=5] [--threshold=68] data/*.jsonl
"""

import json
import sys
from collections import defaultdict

SETTLEMENT_DELAY = 294  # secs_left when market closes

def calculate_no_ask(yes_bid):
    return 100 - yes_bid

def main():
    # Parse args
    mins_before = 5
    threshold = 68
    files = []

    for arg in sys.argv[1:]:
        if arg.startswith("--mins-before-close="):
            mins_before = int(arg.split("=")[1])
        elif arg.startswith("--threshold="):
            threshold = int(arg.split("=")[1])
        else:
            files.append(arg)

    if not files:
        print("Usage: python3 backtest_corrected.py [--mins-before-close=5] [--threshold=68] data/*.jsonl")
        sys.exit(1)

    target_secs_left = SETTLEMENT_DELAY + (mins_before * 60)

    # Group ticks by market ticker
    markets = defaultdict(list)

    for filepath in files:
        with open(filepath, "r") as f:
            for line in f:
                tick = json.loads(line)
                if tick.get("type") != "tick":
                    continue

                for market in tick.get("markets", []):
                    ticker = market["ticker"]
                    strike = market.get("strike", 0)
                    if strike == 0:
                        continue
                    markets[ticker].append({
                        "ts": tick["ts"],
                        "secs_left": market["secs_left"],
                        "yes_bid": market["yes_bid"],
                        "yes_ask": market["yes_ask"],
                        "strike": strike,
                        "brti": tick["brti"],
                        "status": market.get("status", ""),
                        "result": market.get("result", ""),
                    })

    # Process each market
    trades = []
    skipped_no_entry = 0
    skipped_no_settlement = 0

    for ticker, ticks in markets.items():
        ticks.sort(key=lambda t: t["ts"])

        # Find the first tick at or below target secs_left
        # ONLY use ticks before market close (secs_left > SETTLEMENT_DELAY)
        entry_tick = None
        for tick in ticks:
            if tick["secs_left"] <= target_secs_left and tick["secs_left"] > SETTLEMENT_DELAY:
                entry_tick = tick
                break

        if not entry_tick:
            skipped_no_entry += 1
            continue

        yes_ask = entry_tick["yes_ask"]
        no_ask = calculate_no_ask(entry_tick["yes_bid"])

        if yes_ask >= threshold:
            side = "YES"
            entry_price = yes_ask
        elif no_ask >= threshold:
            side = "NO"
            entry_price = no_ask
        else:
            continue  # No trade

        # Find settlement
        settlement_result = ""
        for tick in reversed(ticks):
            result = tick.get("result", "")
            status = tick.get("status", "")
            if result or status in ["finalized", "determined"]:
                settlement_result = result
                if settlement_result:
                    break

        if not settlement_result:
            skipped_no_settlement += 1
            # Fallback: use BRTI at last active tick
            last_active = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY]
            if last_active:
                lt = last_active[-1]
                if lt["brti"] >= lt["strike"]:
                    winner = "YES"
                else:
                    winner = "NO"
            else:
                continue
        else:
            winner = settlement_result.upper()

        if side == winner:
            pnl = 100 - entry_price
        else:
            pnl = -entry_price

        trades.append({
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "winner": winner,
            "pnl": pnl,
            "settlement_source": "kalshi" if settlement_result else "brti",
        })

    if not trades:
        print(f"\nNo trades (no markets with >={threshold}c at {mins_before}min before close)")
        print(f"Skipped (no entry tick): {skipped_no_entry}")
        return

    total_pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = len(trades) - wins

    print(f"\n=== Backtest: {threshold}c threshold @ {mins_before}min before CLOSE ===")
    print(f"  (secs_left target: {target_secs_left}, settlement delay: {SETTLEMENT_DELAY})")
    print(f"Total trades: {len(trades)}")
    print(f"Wins: {wins} ({100*wins/len(trades):.1f}%)")
    print(f"Losses: {losses} ({100*losses/len(trades):.1f}%)")
    print(f"Total P&L: {total_pnl:.2f}c (${total_pnl/100:.2f})")
    print(f"Avg P&L per trade: {total_pnl/len(trades):.2f}c")

    if skipped_no_settlement > 0:
        print(f"Note: {skipped_no_settlement} used BRTI fallback")

    kalshi = sum(1 for t in trades if t["settlement_source"] == "kalshi")
    print(f"Kalshi settlements: {kalshi}/{len(trades)}")
    print()

    print("Individual trades:")
    for t in trades:
        m = "+" if t["settlement_source"] == "kalshi" else "~"
        print(f"  {m} {t['ticker']}: {t['side']} @{t['entry_price']}c -> {t['winner']} wins -> P&L: {t['pnl']:+.0f}c")

if __name__ == "__main__":
    main()
