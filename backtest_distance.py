#!/usr/bin/env python3
"""
Backtest: Price Distance from Strike Strategy

Tests whether the distance between BRTI and the market's strike price
is a profitable signal for trading Kalshi BTC 15-min binary options.

Strategy concept:
- If BTC is $200 above strike, YES is very likely to win
- If BTC is $200 below strike, NO is very likely to win
- The question: at what distance does a trade become +EV?

Tests:
1. Distance-only at various dollar thresholds and time windows
2. Combined: distance + minimum contract price

CRITICAL TIMING:
- secs_left counts to SETTLEMENT, not market close
- Market closes ~294 seconds before settlement
- target_secs_left = 294 + (mins_before_close * 60)
- Only use prices where secs_left > 294

Usage: python3 backtest_distance.py data/*.jsonl
"""

import json
import sys
from collections import defaultdict

# ── Constants ──────────────────────────────────────────────────────────
MARKET_CLOSE_OFFSET = 294  # seconds before settlement when market closes

TIME_WINDOWS_MIN = [3, 5, 7, 10]  # minutes before market close
DISTANCE_THRESHOLDS = [25, 50, 75, 100, 150, 200, 250, 300, 400, 500]

# Combined strategy parameters
COMBO_DISTANCES = [50, 100, 150, 200]
COMBO_PRICES = [60, 65, 68, 70, 75]

# How close (in seconds) to the target time we accept a tick
TICK_TOLERANCE = 10  # accept ticks within +/- 10 seconds of target


def find_entry_tick(ticks, target_secs_left):
    """Find the tick closest to target_secs_left, within tolerance.
    Only considers ticks where secs_left > MARKET_CLOSE_OFFSET (market still open).
    Returns the tick closest to target, or None."""
    best = None
    best_diff = float('inf')
    for tick in ticks:
        if tick['secs_left'] <= MARKET_CLOSE_OFFSET:
            continue  # market already closed
        diff = abs(tick['secs_left'] - target_secs_left)
        if diff < best_diff and diff <= TICK_TOLERANCE:
            best_diff = diff
            best = tick
    return best


def find_settlement(ticks):
    """Search backwards through ticks for settlement result.
    Returns 'yes', 'no', or None."""
    for tick in reversed(ticks):
        result = tick.get('result', '')
        if result in ('yes', 'no'):
            return result
        status = tick.get('status', '')
        if status in ('finalized', 'determined'):
            # Use last BRTI vs strike to determine
            if tick['brti'] >= tick['strike']:
                return 'yes'
            else:
                return 'no'
    return None


def load_data(filepaths):
    """Load JSONL files and group ticks by market ticker."""
    markets = defaultdict(list)
    tick_count = 0
    file_count = 0

    for filepath in filepaths:
        file_count += 1
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if tick.get('type') != 'tick':
                    continue

                tick_count += 1
                brti = tick.get('brti', 0)
                if brti == 0:
                    continue

                for market in tick.get('markets', []):
                    ticker = market['ticker']
                    strike = market.get('strike', 0)
                    if strike == 0:
                        continue

                    markets[ticker].append({
                        'ts': tick['ts'],
                        'secs_left': market['secs_left'],
                        'yes_bid': market.get('yes_bid', 0),
                        'yes_ask': market.get('yes_ask', 0),
                        'strike': strike,
                        'brti': brti,
                        'status': market.get('status', ''),
                        'result': market.get('result', ''),
                    })

    print(f"Loaded {file_count} files, {tick_count} ticks, {len(markets)} unique markets")
    return markets


def prepare_markets(markets_raw):
    """Sort ticks and find settlement for each market. Returns dict of
    ticker -> {'ticks': [...], 'settlement': 'yes'/'no'}"""
    prepared = {}
    settled = 0
    unsettled = 0

    for ticker, ticks in markets_raw.items():
        ticks.sort(key=lambda t: t['ts'])
        settlement = find_settlement(ticks)
        if settlement is None:
            unsettled += 1
            continue
        settled += 1
        prepared[ticker] = {
            'ticks': ticks,
            'settlement': settlement,
        }

    print(f"Markets: {settled} settled, {unsettled} unsettled (skipped)")
    return prepared


def run_distance_backtest(prepared, mins_before, distance_threshold):
    """Run a single distance-only backtest configuration.
    Returns dict with trades, wins, pnl, etc."""
    target_secs = MARKET_CLOSE_OFFSET + (mins_before * 60)

    trades = 0
    wins = 0
    total_pnl = 0
    yes_trades = 0
    no_trades = 0

    for ticker, data in prepared.items():
        ticks = data['ticks']
        settlement = data['settlement']

        entry = find_entry_tick(ticks, target_secs)
        if entry is None:
            continue

        brti = entry['brti']
        strike = entry['strike']
        distance = brti - strike

        yes_ask = entry['yes_ask']
        yes_bid = entry['yes_bid']
        no_ask = 100 - yes_bid  # NO ask = 100 - YES bid

        # Decision based on distance
        if distance >= distance_threshold:
            # BTC is above strike -> buy YES
            if yes_ask <= 0 or yes_ask >= 100:
                continue  # no valid price
            side = 'yes'
            entry_price = yes_ask
            yes_trades += 1
        elif distance <= -distance_threshold:
            # BTC is below strike -> buy NO
            if no_ask <= 0 or no_ask >= 100:
                continue
            side = 'no'
            entry_price = no_ask
            no_trades += 1
        else:
            continue  # distance too small, no trade

        # P&L
        trades += 1
        if side == settlement:
            pnl = 100 - entry_price
            wins += 1
        else:
            pnl = -entry_price
        total_pnl += pnl

    return {
        'trades': trades,
        'wins': wins,
        'winrate': (wins / trades * 100) if trades > 0 else 0,
        'total_pnl': total_pnl,
        'avg_pnl': (total_pnl / trades) if trades > 0 else 0,
        'yes_trades': yes_trades,
        'no_trades': no_trades,
    }


def run_combined_backtest(prepared, mins_before, distance_threshold, min_price):
    """Run a combined backtest: distance threshold AND minimum contract price.
    Only buy if distance > threshold AND the contract price >= min_price."""
    target_secs = MARKET_CLOSE_OFFSET + (mins_before * 60)

    trades = 0
    wins = 0
    total_pnl = 0

    for ticker, data in prepared.items():
        ticks = data['ticks']
        settlement = data['settlement']

        entry = find_entry_tick(ticks, target_secs)
        if entry is None:
            continue

        brti = entry['brti']
        strike = entry['strike']
        distance = brti - strike

        yes_ask = entry['yes_ask']
        yes_bid = entry['yes_bid']
        no_ask = 100 - yes_bid

        # Decision based on distance AND price
        if distance >= distance_threshold:
            # BTC above strike -> buy YES if price meets minimum
            if yes_ask < min_price or yes_ask <= 0 or yes_ask >= 100:
                continue
            side = 'yes'
            entry_price = yes_ask
        elif distance <= -distance_threshold:
            # BTC below strike -> buy NO if price meets minimum
            if no_ask < min_price or no_ask <= 0 or no_ask >= 100:
                continue
            side = 'no'
            entry_price = no_ask
        else:
            continue

        trades += 1
        if side == settlement:
            pnl = 100 - entry_price
            wins += 1
        else:
            pnl = -entry_price
        total_pnl += pnl

    return {
        'trades': trades,
        'wins': wins,
        'winrate': (wins / trades * 100) if trades > 0 else 0,
        'total_pnl': total_pnl,
        'avg_pnl': (total_pnl / trades) if trades > 0 else 0,
    }


def format_pnl(cents):
    """Format P&L in both cents and dollars."""
    return f"{cents:+.0f}c (${cents/100:+.2f})"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 backtest_distance.py data/*.jsonl")
        sys.exit(1)

    # Load and prepare data
    print("=" * 80)
    print("BACKTEST: Price Distance from Strike Strategy")
    print("=" * 80)
    print()

    markets_raw = load_data(sys.argv[1:])
    prepared = prepare_markets(markets_raw)
    print()

    # ── Part 1: Distance-Only Strategies ──────────────────────────────
    print("=" * 80)
    print("PART 1: DISTANCE-ONLY STRATEGIES")
    print("Buy YES if BRTI > strike + $X, Buy NO if BRTI < strike - $X")
    print("=" * 80)

    all_results = []

    for mins in TIME_WINDOWS_MIN:
        print(f"\n{'─' * 80}")
        print(f"  Entry: {mins} min before market close (secs_left = {MARKET_CLOSE_OFFSET + mins * 60})")
        print(f"{'─' * 80}")
        print(f"  {'Dist($)':>8} | {'Trades':>7} | {'Y/N':>7} | {'Wins':>5} | {'WinRate':>8} | {'TotalPnL':>14} | {'AvgPnL':>10}")
        print(f"  {'─'*8}-+-{'─'*7}-+-{'─'*7}-+-{'─'*5}-+-{'─'*8}-+-{'─'*14}-+-{'─'*10}")

        for dist in DISTANCE_THRESHOLDS:
            r = run_distance_backtest(prepared, mins, dist)
            yn = f"{r['yes_trades']}/{r['no_trades']}"
            pnl_str = format_pnl(r['total_pnl'])
            avg_str = f"{r['avg_pnl']:+.1f}c" if r['trades'] > 0 else "n/a"
            print(f"  ${dist:>6} | {r['trades']:>7} | {yn:>7} | {r['wins']:>5} | {r['winrate']:>7.1f}% | {pnl_str:>14} | {avg_str:>10}")

            all_results.append({
                'type': 'distance',
                'mins': mins,
                'distance': dist,
                'min_price': None,
                **r,
            })

    # ── Part 2: Combined Strategies ───────────────────────────────────
    print()
    print("=" * 80)
    print("PART 2: COMBINED STRATEGIES (Distance + Minimum Contract Price)")
    print("Buy only if distance > $X AND contract price >= Yc")
    print("=" * 80)

    for mins in TIME_WINDOWS_MIN:
        print(f"\n{'─' * 80}")
        print(f"  Entry: {mins} min before market close")
        print(f"{'─' * 80}")

        # Header row with price columns
        header = f"  {'Dist($)':>8} |"
        for price in COMBO_PRICES:
            header += f" {price}c{'':>19} |"
        print(header)

        sub_header = f"  {'':>8} |"
        for _ in COMBO_PRICES:
            sub_header += f" {'Trd':>4} {'WR%':>5} {'PnL':>11} |"
        print(sub_header)

        print(f"  {'─' * 8}-+" + (f"-{'─' * 22}-+" * len(COMBO_PRICES)))

        for dist in COMBO_DISTANCES:
            row = f"  ${dist:>6} |"
            for price in COMBO_PRICES:
                r = run_combined_backtest(prepared, mins, dist, price)
                if r['trades'] > 0:
                    pnl_short = f"{r['total_pnl']:+.0f}c"
                    row += f" {r['trades']:>4} {r['winrate']:>5.1f} {pnl_short:>11} |"
                else:
                    row += f" {'--':>4} {'--':>5} {'--':>11} |"

                all_results.append({
                    'type': 'combined',
                    'mins': mins,
                    'distance': dist,
                    'min_price': price,
                    **r,
                })
            print(row)

    # ── Part 3: TOP 10 Strategies ─────────────────────────────────────
    print()
    print("=" * 80)
    print("TOP 10 BEST STRATEGIES (by total P&L, minimum 10 trades)")
    print("=" * 80)

    # Filter to strategies with enough trades
    viable = [r for r in all_results if r['trades'] >= 10]
    viable.sort(key=lambda r: r['total_pnl'], reverse=True)

    print(f"\n  {'#':>3} | {'Type':>10} | {'Mins':>4} | {'Dist($)':>8} | {'MinPx':>6} | {'Trades':>6} | {'WinRate':>8} | {'TotalPnL':>14} | {'AvgPnL':>10}")
    print(f"  {'─'*3}-+-{'─'*10}-+-{'─'*4}-+-{'─'*8}-+-{'─'*6}-+-{'─'*6}-+-{'─'*8}-+-{'─'*14}-+-{'─'*10}")

    for i, r in enumerate(viable[:10], 1):
        tp = r['type']
        px = f"{r['min_price']}c" if r['min_price'] else "  --"
        pnl_str = format_pnl(r['total_pnl'])
        avg_str = f"{r['avg_pnl']:+.1f}c"
        print(f"  {i:>3} | {tp:>10} | {r['mins']:>4} | ${r['distance']:>6} | {px:>6} | {r['trades']:>6} | {r['winrate']:>7.1f}% | {pnl_str:>14} | {avg_str:>10}")

    # ── Part 4: TOP 10 by Avg PnL ────────────────────────────────────
    print()
    print("=" * 80)
    print("TOP 10 BEST STRATEGIES (by avg P&L per trade, minimum 10 trades)")
    print("=" * 80)

    viable.sort(key=lambda r: r['avg_pnl'], reverse=True)

    print(f"\n  {'#':>3} | {'Type':>10} | {'Mins':>4} | {'Dist($)':>8} | {'MinPx':>6} | {'Trades':>6} | {'WinRate':>8} | {'TotalPnL':>14} | {'AvgPnL':>10}")
    print(f"  {'─'*3}-+-{'─'*10}-+-{'─'*4}-+-{'─'*8}-+-{'─'*6}-+-{'─'*6}-+-{'─'*8}-+-{'─'*14}-+-{'─'*10}")

    for i, r in enumerate(viable[:10], 1):
        tp = r['type']
        px = f"{r['min_price']}c" if r['min_price'] else "  --"
        pnl_str = format_pnl(r['total_pnl'])
        avg_str = f"{r['avg_pnl']:+.1f}c"
        print(f"  {i:>3} | {tp:>10} | {r['mins']:>4} | ${r['distance']:>6} | {px:>6} | {r['trades']:>6} | {r['winrate']:>7.1f}% | {pnl_str:>14} | {avg_str:>10}")

    # ── Part 5: TOP 10 by Win Rate ───────────────────────────────────
    print()
    print("=" * 80)
    print("TOP 10 BEST STRATEGIES (by win rate, minimum 10 trades)")
    print("=" * 80)

    viable.sort(key=lambda r: r['winrate'], reverse=True)

    print(f"\n  {'#':>3} | {'Type':>10} | {'Mins':>4} | {'Dist($)':>8} | {'MinPx':>6} | {'Trades':>6} | {'WinRate':>8} | {'TotalPnL':>14} | {'AvgPnL':>10}")
    print(f"  {'─'*3}-+-{'─'*10}-+-{'─'*4}-+-{'─'*8}-+-{'─'*6}-+-{'─'*6}-+-{'─'*8}-+-{'─'*14}-+-{'─'*10}")

    for i, r in enumerate(viable[:10], 1):
        tp = r['type']
        px = f"{r['min_price']}c" if r['min_price'] else "  --"
        pnl_str = format_pnl(r['total_pnl'])
        avg_str = f"{r['avg_pnl']:+.1f}c"
        print(f"  {i:>3} | {tp:>10} | {r['mins']:>4} | ${r['distance']:>6} | {px:>6} | {r['trades']:>6} | {r['winrate']:>7.1f}% | {pnl_str:>14} | {avg_str:>10}")

    # ── Summary Stats ─────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    profitable = [r for r in all_results if r['trades'] >= 10 and r['total_pnl'] > 0]
    losing = [r for r in all_results if r['trades'] >= 10 and r['total_pnl'] <= 0]
    print(f"  Strategies tested:        {len(all_results)}")
    print(f"  With >= 10 trades:        {len(viable)}")
    print(f"  Profitable (pnl > 0):     {len(profitable)}")
    print(f"  Losing (pnl <= 0):        {len(losing)}")
    if profitable:
        best = max(profitable, key=lambda r: r['total_pnl'])
        desc = f"dist=${best['distance']}"
        if best['min_price']:
            desc += f" + >={best['min_price']}c"
        print(f"  Best total PnL:           {format_pnl(best['total_pnl'])} ({desc}, {best['mins']}min, {best['trades']} trades, {best['winrate']:.1f}% WR)")

        best_avg = max(profitable, key=lambda r: r['avg_pnl'])
        desc2 = f"dist=${best_avg['distance']}"
        if best_avg['min_price']:
            desc2 += f" + >={best_avg['min_price']}c"
        print(f"  Best avg PnL/trade:       {best_avg['avg_pnl']:+.1f}c ({desc2}, {best_avg['mins']}min, {best_avg['trades']} trades)")
    print()


if __name__ == '__main__':
    main()
