#!/usr/bin/env python3
"""
Comprehensive EDA on Kalshi BTC 15-minute binary options data.

Analyzes:
1. Market efficiency / calibration at different time windows
2. BTC volatility within 15-min windows
3. Strike price clustering effects
4. Time of day effects
5. Spread analysis
6. Volume as a signal

CRITICAL TIMING:
  secs_left counts to SETTLEMENT, not to market close.
  Markets close ~294 seconds before settlement.
  secs_left = 294 means market close.
  Formula: target_secs_left = 294 + (mins_before_close * 60)
  ONLY use prices where secs_left > 294

Usage: python3 eda_analysis.py data/*.jsonl
"""

import json
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone

SETTLEMENT_DELAY = 294  # secs_left when market closes

# Time windows: minutes before close
TIME_WINDOWS = [10, 7, 5, 3, 1]


def secs_left_for_mins_before_close(mins):
    return SETTLEMENT_DELAY + (mins * 60)


def load_data(files):
    """Load all ticks grouped by market ticker."""
    markets = defaultdict(list)
    tick_count = 0

    for filepath in files:
        with open(filepath, "r") as f:
            for line in f:
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tick.get("type") != "tick":
                    continue
                tick_count += 1
                ts = tick["ts"]
                brti = tick.get("brti", 0)
                for market in tick.get("markets", []):
                    ticker = market["ticker"]
                    markets[ticker].append({
                        "ts": ts,
                        "secs_left": market["secs_left"],
                        "yes_bid": market.get("yes_bid", 0),
                        "yes_ask": market.get("yes_ask", 0),
                        "strike": market.get("strike", 0),
                        "brti": brti,
                        "status": market.get("status", ""),
                        "result": market.get("result", ""),
                        "volume": market.get("volume", 0),
                        "open_interest": market.get("open_interest", 0),
                        "last_price": market.get("last_price", 0),
                    })

    # Sort each market's ticks by timestamp
    for ticker in markets:
        markets[ticker].sort(key=lambda t: t["ts"])

    print(f"Loaded {tick_count} tick records across {len(markets)} unique markets")
    return markets


def get_settlement(ticks):
    """Find settlement result by searching backwards through ticks."""
    for tick in reversed(ticks):
        result = tick.get("result", "")
        status = tick.get("status", "")
        if result:
            return result.lower()
        if status in ["finalized", "determined"]:
            # No explicit result but finalized - use BRTI vs strike
            if tick["strike"] > 0:
                if tick["brti"] >= tick["strike"]:
                    return "yes"
                else:
                    return "no"
    return None


def get_brti_settlement(ticks):
    """Fallback: determine settlement from last known BRTI and strike."""
    last_with_strike = None
    for tick in reversed(ticks):
        if tick["strike"] > 0:
            last_with_strike = tick
            break
    if last_with_strike:
        if last_with_strike["brti"] >= last_with_strike["strike"]:
            return "yes"
        else:
            return "no"
    return None


def get_tick_near_secs(ticks, target_secs, tolerance=30):
    """Find tick closest to target secs_left, within tolerance, and secs_left > SETTLEMENT_DELAY."""
    best = None
    best_diff = float("inf")
    for tick in ticks:
        if tick["secs_left"] <= SETTLEMENT_DELAY:
            continue
        diff = abs(tick["secs_left"] - target_secs)
        if diff < best_diff and diff <= tolerance:
            best_diff = diff
            best = tick
    return best


def parse_hour_from_ticker(ticker):
    """Extract settlement hour from ticker like KXBTC15M-26FEB101245-45."""
    # Format: KXBTC15M-26FEB10HHMM-MM
    try:
        parts = ticker.split("-")
        # parts[1] = '26FEB101245' or similar
        date_part = parts[1]
        # Find the time: last 4 digits before the dash
        time_str = date_part[-4:]
        hour = int(time_str[:2])
        return hour
    except (IndexError, ValueError):
        return None


def parse_ts(ts_str):
    """Parse ISO timestamp to datetime."""
    # Handle nanosecond precision by truncating to microseconds
    if "." in ts_str:
        base, frac = ts_str.split(".")
        frac = frac.rstrip("Z")
        frac = frac[:6]  # truncate to microseconds
        ts_str = f"{base}.{frac}Z"
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


# =============================================================================
# Analysis 1: Market Efficiency / Calibration
# =============================================================================
def analyze_calibration(markets):
    print("\n" + "=" * 80)
    print("1. MARKET EFFICIENCY / CALIBRATION")
    print("=" * 80)
    print("For each time window, group contracts by price bucket.")
    print("Compare market-implied probability to actual win rate.")
    print()

    # Price buckets: 0-10, 10-20, ..., 90-100 (in cents)
    bucket_names = [f"{i*10}-{(i+1)*10}c" for i in range(10)]

    for mins in TIME_WINDOWS:
        target_secs = secs_left_for_mins_before_close(mins)
        print(f"\n--- {mins} min before close (secs_left ~ {target_secs}) ---")

        # Collect YES prices and outcomes
        # We look at both YES ask price and NO ask (= 100 - yes_bid)
        # Each contract gives us TWO data points: the YES side and the NO side
        # But we just use YES ask as the implied probability for YES winning
        buckets = defaultdict(lambda: {"count": 0, "wins": 0, "prices": []})

        settled_count = 0
        for ticker, ticks in markets.items():
            tick = get_tick_near_secs(ticks, target_secs)
            if not tick or tick["strike"] == 0:
                continue

            settlement = get_settlement(ticks)
            if not settlement:
                settlement = get_brti_settlement(ticks)
            if not settlement:
                continue

            settled_count += 1
            yes_ask = tick["yes_ask"]
            yes_bid = tick["yes_bid"]
            no_ask = 100 - yes_bid

            # YES side: price = yes_ask, won if settlement == "yes"
            if 0 < yes_ask <= 100:
                bucket_idx = min(max(0, (yes_ask - 1) // 10), 9)
                buckets[bucket_idx]["count"] += 1
                buckets[bucket_idx]["prices"].append(yes_ask)
                if settlement == "yes":
                    buckets[bucket_idx]["wins"] += 1

            # NO side: price = no_ask, won if settlement == "no"
            if 0 < no_ask <= 100:
                bucket_idx = min(max(0, (no_ask - 1) // 10), 9)
                buckets[bucket_idx]["count"] += 1
                buckets[bucket_idx]["prices"].append(no_ask)
                if settlement == "no":
                    buckets[bucket_idx]["wins"] += 1

        print(f"  Markets with data: {settled_count}")
        print(f"  {'Bucket':<12} {'Count':>6} {'Win%':>7} {'AvgPrice':>9} {'Implied%':>9} {'Edge':>7}")
        print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*9} {'-'*9} {'-'*7}")

        for i in range(10):
            if buckets[i]["count"] == 0:
                continue
            count = buckets[i]["count"]
            wins = buckets[i]["wins"]
            win_pct = 100 * wins / count
            avg_price = sum(buckets[i]["prices"]) / count
            implied_pct = avg_price  # price in cents IS the implied probability %
            edge = win_pct - implied_pct
            print(f"  {bucket_names[i]:<12} {count:>6} {win_pct:>6.1f}% {avg_price:>8.1f}c {implied_pct:>8.1f}% {edge:>+6.1f}%")

        # Also show overall calibration summary
        total_contracts = sum(b["count"] for b in buckets.values())
        total_wins = sum(b["wins"] for b in buckets.values())
        if total_contracts > 0:
            print(f"  Total: {total_contracts} contracts, {100*total_wins/total_contracts:.1f}% win rate")


# =============================================================================
# Analysis 2: BTC Volatility within 15-min windows
# =============================================================================
def analyze_volatility(markets):
    print("\n" + "=" * 80)
    print("2. BTC VOLATILITY WITHIN 15-MIN WINDOWS")
    print("=" * 80)

    ranges = []
    crossings = []

    for ticker, ticks in markets.items():
        # Only use ticks while market is active (secs_left > SETTLEMENT_DELAY)
        active_ticks = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY and t["brti"] > 0]
        if len(active_ticks) < 5:
            continue

        strike = 0
        for t in active_ticks:
            if t["strike"] > 0:
                strike = t["strike"]
                break
        if strike == 0:
            continue

        brti_values = [t["brti"] for t in active_ticks]
        brti_min = min(brti_values)
        brti_max = max(brti_values)
        brti_range = brti_max - brti_min
        brti_start = brti_values[0]

        # Calculate range as % of BTC price
        range_pct = 100 * brti_range / brti_start
        ranges.append({
            "ticker": ticker,
            "range_usd": brti_range,
            "range_pct": range_pct,
            "brti_start": brti_start,
            "strike": strike,
        })

        # Count strike crossings
        cross_count = 0
        prev_side = "above" if brti_values[0] >= strike else "below"
        for brti in brti_values[1:]:
            curr_side = "above" if brti >= strike else "below"
            if curr_side != prev_side:
                cross_count += 1
            prev_side = curr_side
        crossings.append({
            "ticker": ticker,
            "crossings": cross_count,
            "distance_at_start": abs(brti_values[0] - strike),
        })

    if not ranges:
        print("  No data available")
        return

    range_values = [r["range_usd"] for r in ranges]
    range_pct_values = [r["range_pct"] for r in ranges]

    avg_range = sum(range_values) / len(range_values)
    median_range = sorted(range_values)[len(range_values) // 2]
    max_range = max(range_values)
    min_range = min(range_values)
    std_range = (sum((r - avg_range) ** 2 for r in range_values) / len(range_values)) ** 0.5

    print(f"\n  Markets analyzed: {len(ranges)}")
    print(f"\n  BTC Price Range during active market period:")
    print(f"    Average:  ${avg_range:.2f} ({sum(range_pct_values)/len(range_pct_values):.4f}%)")
    print(f"    Median:   ${median_range:.2f}")
    print(f"    Std Dev:  ${std_range:.2f}")
    print(f"    Min:      ${min_range:.2f}")
    print(f"    Max:      ${max_range:.2f}")

    # Distribution of ranges
    print(f"\n  Range distribution:")
    thresholds = [10, 25, 50, 75, 100, 150, 200, 500]
    for thresh in thresholds:
        count = sum(1 for r in range_values if r <= thresh)
        print(f"    <= ${thresh:>4}: {count:>3} ({100*count/len(range_values):.0f}%)")

    # Strike crossings
    cross_values = [c["crossings"] for c in crossings]
    avg_cross = sum(cross_values) / len(cross_values)
    no_cross = sum(1 for c in cross_values if c == 0)
    many_cross = sum(1 for c in cross_values if c >= 3)

    print(f"\n  Strike Price Crossings (BTC crossing the strike during market life):")
    print(f"    Average crossings: {avg_cross:.1f}")
    print(f"    No crossings:      {no_cross}/{len(crossings)} ({100*no_cross/len(crossings):.0f}%)")
    print(f"    3+ crossings:      {many_cross}/{len(crossings)} ({100*many_cross/len(crossings):.0f}%)")

    # Distribution
    for n in range(max(cross_values) + 1):
        count = sum(1 for c in cross_values if c == n)
        if count > 0:
            print(f"    {n} crossings: {count} markets")


# =============================================================================
# Analysis 3: Strike Price Distance Effects
# =============================================================================
def analyze_strike_distance(markets):
    print("\n" + "=" * 80)
    print("3. STRIKE PRICE DISTANCE EFFECTS")
    print("=" * 80)
    print("How does distance from current BTC price to strike affect outcomes?")

    # For each market, calculate distance at various time windows
    for mins in [10, 5, 3, 1]:
        target_secs = secs_left_for_mins_before_close(mins)
        print(f"\n--- {mins} min before close ---")

        entries = []
        for ticker, ticks in markets.items():
            tick = get_tick_near_secs(ticks, target_secs)
            if not tick or tick["strike"] == 0:
                continue

            settlement = get_settlement(ticks)
            if not settlement:
                settlement = get_brti_settlement(ticks)
            if not settlement:
                continue

            distance = tick["brti"] - tick["strike"]
            distance_pct = 100 * distance / tick["brti"] if tick["brti"] > 0 else 0
            yes_ask = tick["yes_ask"]

            entries.append({
                "ticker": ticker,
                "distance_usd": distance,
                "distance_pct": distance_pct,
                "yes_ask": yes_ask,
                "no_ask": 100 - tick["yes_bid"],
                "settlement": settlement,
                "yes_won": settlement == "yes",
            })

        if not entries:
            print("  No data")
            continue

        # Group by distance buckets (in USD)
        dist_buckets = {
            "Far below (<-$100)": lambda d: d < -100,
            "Below (-$100 to -$25)": lambda d: -100 <= d < -25,
            "Slightly below (-$25 to $0)": lambda d: -25 <= d < 0,
            "Slightly above ($0 to $25)": lambda d: 0 <= d < 25,
            "Above ($25 to $100)": lambda d: 25 <= d < 100,
            "Far above (>$100)": lambda d: d >= 100,
        }

        print(f"  {'Distance Bucket':<30} {'N':>4} {'YES%':>6} {'AvgYesAsk':>10} {'AvgNoAsk':>9} {'YesPredPL':>10} {'NoPredPL':>10}")
        print(f"  {'-'*30} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*10} {'-'*10}")

        for bucket_name, bucket_fn in dist_buckets.items():
            bucket_entries = [e for e in entries if bucket_fn(e["distance_usd"])]
            if not bucket_entries:
                continue

            n = len(bucket_entries)
            yes_pct = 100 * sum(1 for e in bucket_entries if e["yes_won"]) / n
            avg_yes_ask = sum(e["yes_ask"] for e in bucket_entries) / n
            avg_no_ask = sum(e["no_ask"] for e in bucket_entries) / n

            # Predicted P&L if you buy YES at yes_ask
            yes_pnl = sum(
                (100 - e["yes_ask"]) if e["yes_won"] else (-e["yes_ask"])
                for e in bucket_entries
            ) / n

            # Predicted P&L if you buy NO at no_ask
            no_pnl = sum(
                (100 - e["no_ask"]) if not e["yes_won"] else (-e["no_ask"])
                for e in bucket_entries
            ) / n

            print(f"  {bucket_name:<30} {n:>4} {yes_pct:>5.1f}% {avg_yes_ask:>9.1f}c {avg_no_ask:>8.1f}c {yes_pnl:>+9.1f}c {no_pnl:>+9.1f}c")

        # Also show: toss-up analysis (YES ask between 40-60)
        tossups = [e for e in entries if 40 <= e["yes_ask"] <= 60]
        non_tossups = [e for e in entries if e["yes_ask"] < 40 or e["yes_ask"] > 60]
        if tossups:
            tu_yes_pct = 100 * sum(1 for e in tossups if e["yes_won"]) / len(tossups)
            print(f"\n  Toss-ups (YES ask 40-60c): {len(tossups)} markets, YES wins {tu_yes_pct:.1f}%")
        if non_tossups:
            nt_yes_pct = 100 * sum(1 for e in non_tossups if e["yes_won"]) / len(non_tossups)
            print(f"  Non-toss-ups:              {len(non_tossups)} markets, YES wins {nt_yes_pct:.1f}%")


# =============================================================================
# Analysis 4: Time of Day Effects
# =============================================================================
def analyze_time_of_day(markets):
    print("\n" + "=" * 80)
    print("4. TIME OF DAY EFFECTS")
    print("=" * 80)

    hourly = defaultdict(lambda: {
        "markets": [],
        "brti_ranges": [],
        "yes_wins": 0,
        "total": 0,
        "volumes": [],
        "spreads": [],
        "crossings": [],
    })

    for ticker, ticks in markets.items():
        hour = parse_hour_from_ticker(ticker)
        if hour is None:
            continue

        # Settlement
        settlement = get_settlement(ticks)
        if not settlement:
            settlement = get_brti_settlement(ticks)
        if not settlement:
            continue

        hourly[hour]["total"] += 1
        if settlement == "yes":
            hourly[hour]["yes_wins"] += 1

        # BTC range during active period
        active = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY and t["brti"] > 0]
        if active:
            brti_vals = [t["brti"] for t in active]
            hourly[hour]["brti_ranges"].append(max(brti_vals) - min(brti_vals))

        # Volume (max volume seen)
        vols = [t["volume"] for t in ticks if t["volume"] > 0]
        if vols:
            hourly[hour]["volumes"].append(max(vols))

        # Spread at ~5 min before close
        tick_5m = get_tick_near_secs(ticks, secs_left_for_mins_before_close(5))
        if tick_5m and tick_5m["yes_ask"] > 0 and tick_5m["yes_bid"] > 0:
            spread = tick_5m["yes_ask"] - tick_5m["yes_bid"]
            hourly[hour]["spreads"].append(spread)

        # Strike crossings
        strike = None
        for t in active:
            if t["strike"] > 0:
                strike = t["strike"]
                break
        if strike and active:
            brti_vals = [t["brti"] for t in active]
            crosses = 0
            prev = "above" if brti_vals[0] >= strike else "below"
            for bv in brti_vals[1:]:
                curr = "above" if bv >= strike else "below"
                if curr != prev:
                    crosses += 1
                prev = curr
            hourly[hour]["crossings"].append(crosses)

    if not hourly:
        print("  No data")
        return

    print(f"\n  {'Hour(UTC)':>9} {'Markets':>8} {'YES%':>6} {'AvgRange':>10} {'AvgSpread':>10} {'AvgVol':>10} {'AvgCross':>9}")
    print(f"  {'-'*9} {'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*10} {'-'*9}")

    for hour in sorted(hourly.keys()):
        h = hourly[hour]
        if h["total"] == 0:
            continue
        yes_pct = 100 * h["yes_wins"] / h["total"]
        avg_range = sum(h["brti_ranges"]) / len(h["brti_ranges"]) if h["brti_ranges"] else 0
        avg_spread = sum(h["spreads"]) / len(h["spreads"]) if h["spreads"] else 0
        avg_vol = sum(h["volumes"]) / len(h["volumes"]) if h["volumes"] else 0
        avg_cross = sum(h["crossings"]) / len(h["crossings"]) if h["crossings"] else 0

        print(f"  {hour:>7}:00 {h['total']:>8} {yes_pct:>5.1f}% ${avg_range:>8.2f} {avg_spread:>9.1f}c {avg_vol:>9.0f} {avg_cross:>8.1f}")


# =============================================================================
# Analysis 5: Spread Analysis
# =============================================================================
def analyze_spreads(markets):
    print("\n" + "=" * 80)
    print("5. SPREAD ANALYSIS")
    print("=" * 80)
    print("Bid-ask spread at different time windows before close.")

    for mins in TIME_WINDOWS:
        target_secs = secs_left_for_mins_before_close(mins)
        spreads = []
        yes_prices = []

        for ticker, ticks in markets.items():
            tick = get_tick_near_secs(ticks, target_secs)
            if not tick or tick["yes_ask"] <= 0 or tick["yes_bid"] <= 0:
                continue

            spread = tick["yes_ask"] - tick["yes_bid"]
            spreads.append(spread)
            yes_prices.append(tick["yes_ask"])

        if not spreads:
            print(f"\n  {mins} min before close: No data")
            continue

        avg_spread = sum(spreads) / len(spreads)
        median_spread = sorted(spreads)[len(spreads) // 2]
        min_spread = min(spreads)
        max_spread = max(spreads)

        print(f"\n  --- {mins} min before close ({len(spreads)} markets) ---")
        print(f"    Avg spread:    {avg_spread:.1f}c")
        print(f"    Median spread: {median_spread}c")
        print(f"    Min spread:    {min_spread}c")
        print(f"    Max spread:    {max_spread}c")

        # Spread by price level
        cheap = [s for s, p in zip(spreads, yes_prices) if p <= 30]
        mid = [s for s, p in zip(spreads, yes_prices) if 30 < p <= 70]
        expensive = [s for s, p in zip(spreads, yes_prices) if p > 70]

        if cheap:
            print(f"    Spread for cheap contracts (YES<=30c): {sum(cheap)/len(cheap):.1f}c ({len(cheap)} markets)")
        if mid:
            print(f"    Spread for mid contracts (30<YES<=70c): {sum(mid)/len(mid):.1f}c ({len(mid)} markets)")
        if expensive:
            print(f"    Spread for expensive contracts (YES>70c): {sum(expensive)/len(expensive):.1f}c ({len(expensive)} markets)")


# =============================================================================
# Analysis 6: Volume as a Signal
# =============================================================================
def analyze_volume(markets):
    print("\n" + "=" * 80)
    print("6. VOLUME AS A SIGNAL")
    print("=" * 80)

    entries = []
    for ticker, ticks in markets.items():
        settlement = get_settlement(ticks)
        if not settlement:
            settlement = get_brti_settlement(ticks)
        if not settlement:
            continue

        # Get max volume for this market
        max_vol = max((t["volume"] for t in ticks), default=0)
        max_oi = max((t["open_interest"] for t in ticks), default=0)

        # Get price at 5 min before close
        tick_5m = get_tick_near_secs(ticks, secs_left_for_mins_before_close(5))
        if not tick_5m or tick_5m["strike"] == 0:
            continue

        distance = abs(tick_5m["brti"] - tick_5m["strike"])
        yes_ask = tick_5m["yes_ask"]

        entries.append({
            "ticker": ticker,
            "volume": max_vol,
            "open_interest": max_oi,
            "settlement": settlement,
            "yes_ask": yes_ask,
            "distance": distance,
        })

    if not entries:
        print("  No data")
        return

    # Sort by volume and split into quartiles
    entries.sort(key=lambda e: e["volume"])
    n = len(entries)

    print(f"\n  Total markets with volume data: {n}")

    # Volume stats
    vols = [e["volume"] for e in entries]
    print(f"  Volume range: {min(vols)} to {max(vols)}")
    print(f"  Avg volume: {sum(vols)/len(vols):.0f}")
    print(f"  Median volume: {sorted(vols)[len(vols)//2]}")

    # Split into high vs low volume
    if n >= 4:
        q_size = n // 4
        quartiles = [
            ("Bottom 25%", entries[:q_size]),
            ("25-50%", entries[q_size:2*q_size]),
            ("50-75%", entries[2*q_size:3*q_size]),
            ("Top 25%", entries[3*q_size:]),
        ]
    else:
        median_idx = n // 2
        quartiles = [
            ("Bottom half", entries[:median_idx]),
            ("Top half", entries[median_idx:]),
        ]

    print(f"\n  {'Quartile':<15} {'N':>4} {'AvgVol':>10} {'AvgYesAsk':>10} {'AvgDist':>10} {'YES%':>6}")
    print(f"  {'-'*15} {'-'*4} {'-'*10} {'-'*10} {'-'*10} {'-'*6}")

    for name, group in quartiles:
        if not group:
            continue
        avg_vol = sum(e["volume"] for e in group) / len(group)
        avg_ya = sum(e["yes_ask"] for e in group) / len(group)
        avg_dist = sum(e["distance"] for e in group) / len(group)
        yes_pct = 100 * sum(1 for e in group if e["settlement"] == "yes") / len(group)
        print(f"  {name:<15} {len(group):>4} {avg_vol:>9.0f} {avg_ya:>9.1f}c ${avg_dist:>8.2f} {yes_pct:>5.1f}%")

    # Volume vs predictability: do high-volume markets have tighter spreads?
    print(f"\n  Volume vs Spread (at 5 min before close):")
    for name, group in quartiles:
        if not group:
            continue
        # Re-check spreads for this group
        group_spreads = []
        for e in group:
            ticks = markets[e["ticker"]]
            tick_5m = get_tick_near_secs(ticks, secs_left_for_mins_before_close(5))
            if tick_5m and tick_5m["yes_ask"] > 0 and tick_5m["yes_bid"] > 0:
                group_spreads.append(tick_5m["yes_ask"] - tick_5m["yes_bid"])
        if group_spreads:
            avg_spread = sum(group_spreads) / len(group_spreads)
            print(f"    {name:<15}: avg spread {avg_spread:.1f}c")


# =============================================================================
# Analysis 7: Detailed P&L simulation per strategy
# =============================================================================
def analyze_strategies(markets):
    print("\n" + "=" * 80)
    print("7. STRATEGY SIMULATIONS")
    print("=" * 80)
    print("Simulating different strategies to find +EV edges.")

    strategies = [
        # (name, mins_before_close, buy_side_fn, description)
        # buy_side_fn takes (yes_ask, no_ask, distance, brti, strike) -> side or None
    ]

    # Strategy A: Buy high-confidence (>= 70c) at various times
    for mins in [10, 7, 5, 3]:
        for threshold in [65, 70, 75, 80, 85]:
            strategy_results = []
            target_secs = secs_left_for_mins_before_close(mins)

            for ticker, ticks in markets.items():
                tick = get_tick_near_secs(ticks, target_secs)
                if not tick or tick["strike"] == 0:
                    continue

                settlement = get_settlement(ticks)
                if not settlement:
                    settlement = get_brti_settlement(ticks)
                if not settlement:
                    continue

                yes_ask = tick["yes_ask"]
                no_ask = 100 - tick["yes_bid"]

                # Buy whichever side is >= threshold
                side = None
                entry_price = 0
                if yes_ask >= threshold and yes_ask < 99:
                    side = "YES"
                    entry_price = yes_ask
                elif no_ask >= threshold and no_ask < 99:
                    side = "NO"
                    entry_price = no_ask

                if side is None:
                    continue

                won = (side.lower() == settlement)
                pnl = (100 - entry_price) if won else (-entry_price)
                strategy_results.append({
                    "entry_price": entry_price,
                    "won": won,
                    "pnl": pnl,
                })

            strategies.append((f"Buy>={threshold}c@{mins}m", mins, threshold, strategy_results))

    # Print strategy comparison table
    print(f"\n  {'Strategy':<22} {'Trades':>7} {'WinRate':>8} {'AvgPnL':>8} {'TotalPnL':>10} {'AvgEntry':>9}")
    print(f"  {'-'*22} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*9}")

    for name, mins, threshold, results in strategies:
        if not results:
            continue
        n = len(results)
        wins = sum(1 for r in results if r["won"])
        total_pnl = sum(r["pnl"] for r in results)
        avg_pnl = total_pnl / n
        avg_entry = sum(r["entry_price"] for r in results) / n
        win_rate = 100 * wins / n
        # Highlight positive PnL
        marker = " ***" if avg_pnl > 0 else ""
        print(f"  {name:<22} {n:>7} {win_rate:>7.1f}% {avg_pnl:>+7.1f}c {total_pnl:>+9.0f}c {avg_entry:>8.1f}c{marker}")

    # Strategy B: Fade overpriced contracts (sell high-priced YES when BTC trending away)
    print(f"\n  --- Fade Strategies (buy cheap side) ---")
    print(f"  {'Strategy':<30} {'Trades':>7} {'WinRate':>8} {'AvgPnL':>8} {'TotalPnL':>10}")
    print(f"  {'-'*30} {'-'*7} {'-'*8} {'-'*8} {'-'*10}")

    for mins in [5, 3, 1]:
        target_secs = secs_left_for_mins_before_close(mins)
        for max_price in [20, 25, 30, 35]:
            fade_results = []
            for ticker, ticks in markets.items():
                tick = get_tick_near_secs(ticks, target_secs)
                if not tick or tick["strike"] == 0:
                    continue

                settlement = get_settlement(ticks)
                if not settlement:
                    settlement = get_brti_settlement(ticks)
                if not settlement:
                    continue

                yes_ask = tick["yes_ask"]
                no_ask = 100 - tick["yes_bid"]

                # Buy the cheap side (contrarian)
                side = None
                entry_price = 0
                if yes_ask <= max_price and yes_ask > 1:
                    side = "YES"
                    entry_price = yes_ask
                elif no_ask <= max_price and no_ask > 1:
                    side = "NO"
                    entry_price = no_ask

                if side is None:
                    continue

                won = (side.lower() == settlement)
                pnl = (100 - entry_price) if won else (-entry_price)
                fade_results.append({"pnl": pnl, "won": won, "entry_price": entry_price})

            if fade_results:
                n = len(fade_results)
                wins = sum(1 for r in fade_results if r["won"])
                total_pnl = sum(r["pnl"] for r in fade_results)
                avg_pnl = total_pnl / n
                win_rate = 100 * wins / n
                marker = " ***" if avg_pnl > 0 else ""
                print(f"  Buy<={max_price}c@{mins}m-before-close  {n:>7} {win_rate:>7.1f}% {avg_pnl:>+7.1f}c {total_pnl:>+9.0f}c{marker}")


# =============================================================================
# Analysis 8: Raw data dump for eyeballing
# =============================================================================
def analyze_raw_summary(markets):
    print("\n" + "=" * 80)
    print("8. RAW MARKET SUMMARY")
    print("=" * 80)

    rows = []
    for ticker, ticks in sorted(markets.items()):
        settlement = get_settlement(ticks)
        source = "kalshi"
        if not settlement:
            settlement = get_brti_settlement(ticks)
            source = "brti"
        if not settlement:
            settlement = "?"
            source = "none"

        # Key stats
        active = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY]
        strike = 0
        for t in ticks:
            if t["strike"] > 0:
                strike = t["strike"]
                break

        max_vol = max((t["volume"] for t in ticks), default=0)
        max_oi = max((t["open_interest"] for t in ticks), default=0)

        # Price at 5 min and 1 min before close
        t5 = get_tick_near_secs(ticks, secs_left_for_mins_before_close(5))
        t1 = get_tick_near_secs(ticks, secs_left_for_mins_before_close(1))

        ya5 = t5["yes_ask"] if t5 else 0
        ya1 = t1["yes_ask"] if t1 else 0

        brti_at_5 = t5["brti"] if t5 else 0
        dist_5 = brti_at_5 - strike if brti_at_5 > 0 and strike > 0 else 0

        rows.append({
            "ticker": ticker,
            "strike": strike,
            "settlement": settlement,
            "source": source,
            "volume": max_vol,
            "oi": max_oi,
            "ya_5m": ya5,
            "ya_1m": ya1,
            "dist_5m": dist_5,
            "n_ticks": len(ticks),
            "n_active": len(active),
        })

    print(f"\n  {'Ticker':<30} {'Strike':>10} {'Result':>7} {'Src':>5} {'Vol':>7} {'OI':>6} {'YA@5m':>6} {'YA@1m':>6} {'Dist@5m':>9}")
    print(f"  {'-'*30} {'-'*10} {'-'*7} {'-'*5} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*9}")

    for r in rows:
        print(f"  {r['ticker']:<30} {r['strike']:>10.2f} {r['settlement']:>7} {r['source']:>5} {r['volume']:>7} {r['oi']:>6} {r['ya_5m']:>5}c {r['ya_1m']:>5}c ${r['dist_5m']:>+8.2f}")


# =============================================================================
# Main
# =============================================================================
def main():
    files = [f for f in sys.argv[1:] if not f.startswith("--")]
    if not files:
        print("Usage: python3 eda_analysis.py data/*.jsonl")
        sys.exit(1)

    print("=" * 80)
    print("BTC 15-MINUTE BINARY OPTIONS - COMPREHENSIVE EDA")
    print("=" * 80)
    print(f"Files: {files}")

    markets = load_data(files)

    # Run all analyses
    analyze_raw_summary(markets)
    analyze_calibration(markets)
    analyze_volatility(markets)
    analyze_strike_distance(markets)
    analyze_time_of_day(markets)
    analyze_spreads(markets)
    analyze_volume(markets)
    analyze_strategies(markets)

    print("\n" + "=" * 80)
    print("END OF ANALYSIS")
    print("=" * 80)


if __name__ == "__main__":
    main()
