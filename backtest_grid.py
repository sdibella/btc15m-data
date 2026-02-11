#!/usr/bin/env python3
"""
Comprehensive grid search backtest for Kalshi BTC 15-minute binary options.

Tests ALL combinations of:
- Thresholds: 55, 60, 65, 68, 70, 75, 80, 85, 90, 95 cents
- Minutes before close: 1..12

CRITICAL TIMING:
- secs_left counts to SETTLEMENT, not market close
- Markets close ~294s before settlement
- secs_left = 294 means market close (trading stops)
- Formula: target_secs_left = 294 + (mins_before_close * 60)
- Only use prices where secs_left > 294

Strategy logic per (threshold, minutes) combo:
- At N minutes before close, find the tick closest to target_secs_left
- If YES ask >= threshold, buy YES
- If NO ask >= threshold (NO ask = 100 - yes_bid), buy NO
- If both qualify, pick the higher-priced side (more confident)
- Hold until settlement
- P&L: Win = 100 - entry_price, Loss = -entry_price

Usage: python3 backtest_grid.py data/*.jsonl
"""

import json
import sys
import math
from collections import defaultdict


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 backtest_grid.py data/*.jsonl", file=sys.stderr)
        sys.exit(1)

    # --- Configuration ---
    THRESHOLDS = [55, 60, 65, 68, 70, 75, 80, 85, 90, 95]
    MINUTES_BEFORE_CLOSE = list(range(1, 13))  # 1..12
    SETTLEMENT_OFFSET = 294  # secs_left when market closes

    # --- Phase 1: Load all data, group by market ticker ---
    print("Loading data...", file=sys.stderr)
    markets = defaultdict(list)
    file_count = 0

    for filepath in sys.argv[1:]:
        file_count += 1
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                tick = json.loads(line)
                if tick.get('type') != 'tick':
                    continue

                ts = tick['ts']
                brti = tick['brti']

                for market in tick.get('markets', []):
                    ticker = market['ticker']
                    strike = market.get('strike', 0)
                    if strike == 0:
                        continue

                    markets[ticker].append({
                        'ts': ts,
                        'secs_left': market['secs_left'],
                        'yes_bid': market['yes_bid'],
                        'yes_ask': market['yes_ask'],
                        'strike': strike,
                        'brti': brti,
                        'status': market.get('status', ''),
                        'result': market.get('result', ''),
                    })

    print(f"Loaded {file_count} files, {len(markets)} unique markets", file=sys.stderr)

    # --- Phase 2: Pre-process each market: sort, find settlement ---
    print("Pre-processing markets...", file=sys.stderr)
    market_data = {}  # ticker -> {ticks, settlement_result}

    for ticker, ticks in markets.items():
        ticks.sort(key=lambda t: t['ts'])

        # Find settlement by searching backwards for result field
        settlement_result = None
        for t in reversed(ticks):
            if t['result'] in ('yes', 'no'):
                settlement_result = t['result'].upper()
                break
            if t['status'] in ('finalized', 'determined'):
                # Determine from BRTI vs strike
                if t['brti'] >= t['strike']:
                    settlement_result = 'YES'
                else:
                    settlement_result = 'NO'
                break

        if settlement_result is None:
            # Fall back: use last tick's BRTI vs strike
            last = ticks[-1]
            if last['brti'] >= last['strike']:
                settlement_result = 'YES'
            else:
                settlement_result = 'NO'

        market_data[ticker] = {
            'ticks': ticks,
            'settlement': settlement_result,
        }

    # --- Phase 3: Grid search ---
    print("Running grid search...", file=sys.stderr)

    # Pre-compute target secs_left for each minutes_before_close
    target_secs = {}
    for mins in MINUTES_BEFORE_CLOSE:
        target_secs[mins] = SETTLEMENT_OFFSET + (mins * 60)

    # Results: (threshold, mins) -> list of pnl values
    results = defaultdict(list)

    for ticker, data in market_data.items():
        ticks = data['ticks']
        settlement = data['settlement']

        # For each timing, find the best entry tick
        for mins in MINUTES_BEFORE_CLOSE:
            target = target_secs[mins]
            # Find tick closest to target_secs_left, but must be > 294 (active trading)
            best_tick = None
            best_diff = float('inf')

            for t in ticks:
                if t['secs_left'] <= SETTLEMENT_OFFSET:
                    continue  # Market closed, skip
                diff = abs(t['secs_left'] - target)
                if diff < best_diff:
                    best_diff = diff
                    best_tick = t

            if best_tick is None:
                continue
            # Only accept ticks within 30s of target
            if best_diff > 30:
                continue

            yes_ask = best_tick['yes_ask']
            no_ask = 100 - best_tick['yes_bid']

            # For each threshold, check if trade qualifies
            for thresh in THRESHOLDS:
                side = None
                entry_price = None

                # Pick the side that qualifies; if both, pick higher (more confident)
                yes_qualifies = yes_ask >= thresh and yes_ask <= 99
                no_qualifies = no_ask >= thresh and no_ask <= 99

                if yes_qualifies and no_qualifies:
                    if yes_ask >= no_ask:
                        side = 'YES'
                        entry_price = yes_ask
                    else:
                        side = 'NO'
                        entry_price = no_ask
                elif yes_qualifies:
                    side = 'YES'
                    entry_price = yes_ask
                elif no_qualifies:
                    side = 'NO'
                    entry_price = no_ask
                else:
                    continue  # No trade

                # Calculate P&L
                if side == settlement:
                    pnl = 100 - entry_price
                else:
                    pnl = -entry_price

                results[(thresh, mins)].append(pnl)

    # --- Phase 4: Compute statistics and output ---
    print("\n" + "=" * 120)
    print("GRID SEARCH RESULTS: Kalshi BTC 15-min Binary Options")
    print("=" * 120)
    print(f"Markets analyzed: {len(market_data)}")
    print(f"Thresholds: {THRESHOLDS}")
    print(f"Minutes before close: {MINUTES_BEFORE_CLOSE}")
    print(f"Settlement offset: {SETTLEMENT_OFFSET}s")
    print("=" * 120)

    summary_rows = []

    # Print header
    print(f"\n{'Thresh':>6} {'Mins':>4} {'Trades':>7} {'Wins':>5} {'WinRate':>8} "
          f"{'TotalPnL':>10} {'AvgPnL':>8} {'StdPnL':>8} {'Sharpe':>8} {'AvgEntry':>8}")
    print("-" * 100)

    for thresh in THRESHOLDS:
        for mins in MINUTES_BEFORE_CLOSE:
            pnls = results.get((thresh, mins), [])
            n = len(pnls)
            if n == 0:
                print(f"{thresh:>6} {mins:>4} {'--':>7}")
                continue

            total_pnl = sum(pnls)
            avg_pnl = total_pnl / n
            wins = sum(1 for p in pnls if p > 0)
            win_rate = 100.0 * wins / n

            # Standard deviation
            if n > 1:
                variance = sum((p - avg_pnl) ** 2 for p in pnls) / (n - 1)
                std_pnl = math.sqrt(variance)
            else:
                std_pnl = 0.0

            # Sharpe-like: avg / std
            sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0.0

            # Average entry price (recover from pnl values)
            # Win: pnl = 100 - entry -> entry = 100 - pnl
            # Loss: pnl = -entry -> entry = -pnl
            entries = []
            for p in pnls:
                if p > 0:
                    entries.append(100 - p)
                else:
                    entries.append(-p)
            avg_entry = sum(entries) / len(entries)

            row = {
                'thresh': thresh,
                'mins': mins,
                'trades': n,
                'wins': wins,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'avg_pnl': avg_pnl,
                'std_pnl': std_pnl,
                'sharpe': sharpe,
                'avg_entry': avg_entry,
            }
            summary_rows.append(row)

            print(f"{thresh:>6} {mins:>4} {n:>7} {wins:>5} {win_rate:>7.1f}% "
                  f"{total_pnl:>+10.0f} {avg_pnl:>+8.2f} {std_pnl:>8.2f} {sharpe:>+8.3f} {avg_entry:>8.1f}")

    # --- TOP 10 by Total P&L ---
    print("\n" + "=" * 120)
    print("TOP 10 BY TOTAL P&L")
    print("=" * 120)
    top_total = sorted(summary_rows, key=lambda r: r['total_pnl'], reverse=True)[:10]
    print(f"{'Rank':>4} {'Thresh':>6} {'Mins':>4} {'Trades':>7} {'WinRate':>8} "
          f"{'TotalPnL':>10} {'AvgPnL':>8} {'Sharpe':>8} {'AvgEntry':>8}")
    print("-" * 80)
    for i, row in enumerate(top_total, 1):
        print(f"{i:>4} {row['thresh']:>6} {row['mins']:>4} {row['trades']:>7} {row['win_rate']:>7.1f}% "
              f"{row['total_pnl']:>+10.0f} {row['avg_pnl']:>+8.2f} {row['sharpe']:>+8.3f} {row['avg_entry']:>8.1f}")

    # --- TOP 10 by Avg P&L per trade ---
    print("\n" + "=" * 120)
    print("TOP 10 BY AVG P&L PER TRADE (min 10 trades)")
    print("=" * 120)
    qualified = [r for r in summary_rows if r['trades'] >= 10]
    top_avg = sorted(qualified, key=lambda r: r['avg_pnl'], reverse=True)[:10]
    print(f"{'Rank':>4} {'Thresh':>6} {'Mins':>4} {'Trades':>7} {'WinRate':>8} "
          f"{'TotalPnL':>10} {'AvgPnL':>8} {'Sharpe':>8} {'AvgEntry':>8}")
    print("-" * 80)
    for i, row in enumerate(top_avg, 1):
        print(f"{i:>4} {row['thresh']:>6} {row['mins']:>4} {row['trades']:>7} {row['win_rate']:>7.1f}% "
              f"{row['total_pnl']:>+10.0f} {row['avg_pnl']:>+8.2f} {row['sharpe']:>+8.3f} {row['avg_entry']:>8.1f}")

    # --- TOP 10 by Sharpe ---
    print("\n" + "=" * 120)
    print("TOP 10 BY SHARPE RATIO (min 10 trades)")
    print("=" * 120)
    top_sharpe = sorted(qualified, key=lambda r: r['sharpe'], reverse=True)[:10]
    print(f"{'Rank':>4} {'Thresh':>6} {'Mins':>4} {'Trades':>7} {'WinRate':>8} "
          f"{'TotalPnL':>10} {'AvgPnL':>8} {'Sharpe':>8} {'AvgEntry':>8}")
    print("-" * 80)
    for i, row in enumerate(top_sharpe, 1):
        print(f"{i:>4} {row['thresh']:>6} {row['mins']:>4} {row['trades']:>7} {row['win_rate']:>7.1f}% "
              f"{row['total_pnl']:>+10.0f} {row['avg_pnl']:>+8.2f} {row['sharpe']:>+8.3f} {row['avg_entry']:>8.1f}")

    # --- WORST 10 ---
    print("\n" + "=" * 120)
    print("WORST 10 BY TOTAL P&L")
    print("=" * 120)
    worst = sorted(summary_rows, key=lambda r: r['total_pnl'])[:10]
    print(f"{'Rank':>4} {'Thresh':>6} {'Mins':>4} {'Trades':>7} {'WinRate':>8} "
          f"{'TotalPnL':>10} {'AvgPnL':>8} {'Sharpe':>8} {'AvgEntry':>8}")
    print("-" * 80)
    for i, row in enumerate(worst, 1):
        print(f"{i:>4} {row['thresh']:>6} {row['mins']:>4} {row['trades']:>7} {row['win_rate']:>7.1f}% "
              f"{row['total_pnl']:>+10.0f} {row['avg_pnl']:>+8.2f} {row['sharpe']:>+8.3f} {row['avg_entry']:>8.1f}")

    print("\n" + "=" * 120)
    print("NOTES:")
    print(f"  - All prices in cents. TotalPnL in cents.")
    print(f"  - Sharpe = avg_pnl / std_pnl (higher is better)")
    print(f"  - Entry prices capped at 99c (no 100c entries)")
    print(f"  - Settlement offset: {SETTLEMENT_OFFSET}s (market close)")
    print(f"  - Tick tolerance: 30s window around target time")
    print("=" * 120)


if __name__ == '__main__':
    main()
