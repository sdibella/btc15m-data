#!/usr/bin/env python3
"""
Backtest three unconventional strategies on Kalshi BTC 15-minute binary options.

Strategy A: CONTRARIAN / BUY THE CHEAP SIDE
  Buy the underdog when it's cheap. Huge payoff on win offsets multiple losses.

Strategy B: MOMENTUM / PRICE MOVEMENT
  If a contract price is moving rapidly in one direction, jump on.

Strategy C: EXCHANGE FEED SIGNAL
  Use exchange price feeds to detect BTC moves before Kalshi reacts.

CRITICAL TIMING:
  secs_left counts to SETTLEMENT, not market close.
  Market close ~ 294 seconds before settlement.
  target_secs_left = 294 + (mins_before_close * 60)
  ONLY use prices where secs_left > 294

Usage: python3 backtest_strategies.py data/*.jsonl
"""

import json
import sys
from collections import defaultdict


SETTLEMENT_DELAY = 294  # secs_left when market closes


def load_data(files):
    """Load all JSONL files, group ticks by market ticker."""
    markets = defaultdict(list)
    for filepath in files:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                tick = json.loads(line)
                if tick.get("type") != "tick":
                    continue

                ts = tick["ts"]
                brti = tick.get("brti", 0)
                coinbase = tick.get("coinbase", 0)
                kraken = tick.get("kraken", 0)
                bitstamp = tick.get("bitstamp", 0)
                binance = tick.get("binance", 0)

                for market in tick.get("markets", []):
                    ticker = market["ticker"]
                    strike = market.get("strike", 0)
                    if strike == 0:
                        continue
                    markets[ticker].append({
                        "ts": ts,
                        "secs_left": market["secs_left"],
                        "yes_bid": market["yes_bid"],
                        "yes_ask": market["yes_ask"],
                        "strike": strike,
                        "brti": brti,
                        "coinbase": coinbase,
                        "kraken": kraken,
                        "bitstamp": bitstamp,
                        "binance": binance,
                        "status": market.get("status", ""),
                        "result": market.get("result", ""),
                    })

    # Sort each market's ticks by timestamp
    for ticker in markets:
        markets[ticker].sort(key=lambda t: t["ts"])

    return markets


def find_settlement(ticks):
    """Find settlement result for a market. Returns 'yes' or 'no' or ''."""
    for tick in reversed(ticks):
        result = tick.get("result", "")
        if result:
            return result.lower()
        status = tick.get("status", "")
        if status in ["finalized", "determined"]:
            # Keep looking for an actual result
            continue

    # Fallback: use BRTI from last active tick
    active_ticks = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY]
    if active_ticks:
        lt = active_ticks[-1]
        if lt["brti"] >= lt["strike"]:
            return "yes"
        else:
            return "no"

    return ""


def find_tick_at_time(ticks, target_secs_left, tolerance=30):
    """Find the first tick at or just below target_secs_left (still above market close).
    Returns None if no suitable tick found."""
    for tick in ticks:
        if tick["secs_left"] <= target_secs_left and tick["secs_left"] > SETTLEMENT_DELAY:
            # Check it's within tolerance of the target
            if target_secs_left - tick["secs_left"] <= tolerance:
                return tick
            else:
                return tick  # Best we have
    return None


def find_tick_at_secs_left(ticks, target_secs_left, tolerance=15):
    """Find tick closest to a specific secs_left value. Must be > SETTLEMENT_DELAY."""
    best = None
    best_diff = float("inf")
    for tick in ticks:
        if tick["secs_left"] <= SETTLEMENT_DELAY:
            continue
        diff = abs(tick["secs_left"] - target_secs_left)
        if diff < best_diff:
            best_diff = diff
            best = tick
    if best and best_diff <= tolerance:
        return best
    # Relax tolerance - find closest available
    if best and best_diff <= 60:
        return best
    return None


# =============================================================================
# STRATEGY A: CONTRARIAN / BUY THE CHEAP SIDE
# =============================================================================

def strategy_a(markets):
    """Buy the cheap side (underdog) when price <= threshold."""
    print("=" * 80)
    print("STRATEGY A: CONTRARIAN / BUY THE CHEAP SIDE")
    print("=" * 80)
    print()
    print("Logic: Buy whichever side is CHEAP (underdog). One win at 20c = +80c,")
    print("       offsetting 4 losses at 20c each.")
    print()

    timing_mins = [3, 5, 7, 10]
    max_prices = [15, 20, 25, 30, 35, 40]

    # Header
    print(f"{'Mins':>4} {'MaxP':>4} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
          f"{'TotPnL':>8} {'AvgPnL':>7} {'AvgWin':>7} {'AvgLoss':>7}")
    print("-" * 75)

    best_avg_pnl = -999
    best_config = ""
    all_results = []

    for mins in timing_mins:
        target_secs = SETTLEMENT_DELAY + (mins * 60)
        for max_price in max_prices:
            trades = []

            for ticker, ticks in markets.items():
                entry_tick = find_tick_at_time(ticks, target_secs)
                if not entry_tick:
                    continue

                yes_ask = entry_tick["yes_ask"]
                yes_bid = entry_tick["yes_bid"]
                no_ask = 100 - yes_bid  # NO ask = 100 - YES bid

                # Buy whichever side is cheap (at or below max_price)
                side = None
                entry_price = None

                if yes_ask <= max_price and yes_ask > 0:
                    side = "yes"
                    entry_price = yes_ask
                elif no_ask <= max_price and no_ask > 0:
                    side = "no"
                    entry_price = no_ask
                else:
                    continue  # Neither side is cheap enough

                # Find settlement
                winner = find_settlement(ticks)
                if not winner:
                    continue

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
                })

            if not trades:
                print(f"{mins:>4} {max_price:>4} {'--':>6}")
                continue

            total_pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            win_rate = 100 * len(wins) / len(trades)
            avg_pnl = total_pnl / len(trades)
            avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

            result = {
                "mins": mins, "max_price": max_price,
                "trades": len(trades), "wins": len(wins),
                "win_rate": win_rate, "total_pnl": total_pnl,
                "avg_pnl": avg_pnl, "avg_win": avg_win, "avg_loss": avg_loss,
            }
            all_results.append(result)

            if avg_pnl > best_avg_pnl and len(trades) >= 5:
                best_avg_pnl = avg_pnl
                best_config = f"{mins}min, max {max_price}c"

            print(f"{mins:>4} {max_price:>4} {len(trades):>6} {len(wins):>5} "
                  f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c "
                  f"{avg_win:>6.1f}c {avg_loss:>6.1f}c")

    print()
    if best_config:
        print(f"  BEST CONFIG (min 5 trades): {best_config} -> avg P&L {best_avg_pnl:.1f}c/trade")
    print()

    return all_results


# =============================================================================
# STRATEGY B: MOMENTUM / PRICE MOVEMENT
# =============================================================================

def strategy_b(markets):
    """Buy based on recent price movement direction."""
    print("=" * 80)
    print("STRATEGY B: MOMENTUM / PRICE MOVEMENT")
    print("=" * 80)
    print()
    print("Logic: At M minutes before close, compare YES ask now vs K seconds ago.")
    print("       If price moved up strongly -> buy YES. If down -> buy NO.")
    print("       Min contract price 55c (avoid uncertain markets).")
    print()

    entry_mins = [5, 7, 10]
    lookbacks = [30, 60, 120, 180, 300]
    thresholds = [3, 5, 8, 10, 15, 20]

    best_avg_pnl = -999
    best_config = ""
    all_results = []

    for entry_min in entry_mins:
        print(f"\n--- Entry: {entry_min} min before close ---")
        print(f"{'LB_s':>5} {'Thr':>4} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
              f"{'TotPnL':>8} {'AvgPnL':>7} {'AvgWin':>7} {'AvgLoss':>7}")
        print("-" * 75)

        target_secs = SETTLEMENT_DELAY + (entry_min * 60)

        for lookback in lookbacks:
            lookback_secs = target_secs + lookback  # earlier tick

            for threshold in thresholds:
                trades = []

                for ticker, ticks in markets.items():
                    # Find current tick (at entry time)
                    now_tick = find_tick_at_time(ticks, target_secs)
                    if not now_tick:
                        continue

                    # Find earlier tick (lookback seconds before)
                    earlier_tick = find_tick_at_secs_left(ticks, lookback_secs, tolerance=30)
                    if not earlier_tick:
                        continue

                    yes_ask_now = now_tick["yes_ask"]
                    yes_ask_before = earlier_tick["yes_ask"]

                    if yes_ask_now == 0 or yes_ask_before == 0:
                        continue

                    price_change = yes_ask_now - yes_ask_before

                    # Determine trade direction
                    side = None
                    entry_price = None

                    if price_change >= threshold:
                        # YES price rising -> buy YES
                        no_ask = 100 - now_tick["yes_bid"]
                        # Min price check: YES ask must be >= 55
                        if yes_ask_now >= 55:
                            side = "yes"
                            entry_price = yes_ask_now
                    elif price_change <= -threshold:
                        # YES price falling -> NO is gaining -> buy NO
                        no_ask = 100 - now_tick["yes_bid"]
                        if no_ask >= 55:
                            side = "no"
                            entry_price = no_ask

                    if side is None:
                        continue

                    winner = find_settlement(ticks)
                    if not winner:
                        continue

                    if side == winner:
                        pnl = 100 - entry_price
                    else:
                        pnl = -entry_price

                    trades.append({
                        "ticker": ticker,
                        "side": side,
                        "entry_price": entry_price,
                        "price_change": price_change,
                        "winner": winner,
                        "pnl": pnl,
                    })

                if not trades:
                    continue

                total_pnl = sum(t["pnl"] for t in trades)
                wins = [t for t in trades if t["pnl"] > 0]
                losses = [t for t in trades if t["pnl"] <= 0]
                win_rate = 100 * len(wins) / len(trades)
                avg_pnl = total_pnl / len(trades)
                avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
                avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

                result = {
                    "entry_min": entry_min, "lookback": lookback,
                    "threshold": threshold, "trades": len(trades),
                    "wins": len(wins), "win_rate": win_rate,
                    "total_pnl": total_pnl, "avg_pnl": avg_pnl,
                    "avg_win": avg_win, "avg_loss": avg_loss,
                }
                all_results.append(result)

                if avg_pnl > best_avg_pnl and len(trades) >= 5:
                    best_avg_pnl = avg_pnl
                    best_config = f"{entry_min}min, {lookback}s lookback, {threshold}c threshold"

                print(f"{lookback:>5} {threshold:>4} {len(trades):>6} {len(wins):>5} "
                      f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c "
                      f"{avg_win:>6.1f}c {avg_loss:>6.1f}c")

    print()
    if best_config:
        print(f"  BEST CONFIG (min 5 trades): {best_config} -> avg P&L {best_avg_pnl:.1f}c/trade")
    print()

    return all_results


# =============================================================================
# STRATEGY C: EXCHANGE FEED SIGNAL
# =============================================================================

def strategy_c(markets):
    """Use exchange feeds to detect BTC moves before Kalshi reacts."""
    print("=" * 80)
    print("STRATEGY C: EXCHANGE FEED SIGNAL")
    print("=" * 80)
    print()
    print("Logic: Compare avg exchange price to strike. If exchanges agree on")
    print("       direction vs strike, trade that direction. Check if exchange")
    print("       signal disagrees with Kalshi pricing for extra edge.")
    print()

    entry_mins = [3, 5, 7, 10]

    # We'll test multiple sub-strategies:
    # C1: Pure exchange signal (avg exchange vs strike)
    # C2: Exchange signal DISAGREES with Kalshi pricing (contrarian)
    # C3: Exchange signal strength (how far avg is from strike)

    print("--- C1: PURE EXCHANGE SIGNAL (avg exchange vs strike) ---")
    print(f"{'Mins':>4} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
          f"{'TotPnL':>8} {'AvgPnL':>7}")
    print("-" * 50)

    best_avg_pnl = -999
    best_config = ""
    all_results = []

    for mins in entry_mins:
        target_secs = SETTLEMENT_DELAY + (mins * 60)
        trades = []

        for ticker, ticks in markets.items():
            entry_tick = find_tick_at_time(ticks, target_secs)
            if not entry_tick:
                continue

            # Calculate avg exchange price (skip zeros)
            feeds = []
            for feed in ["coinbase", "kraken", "bitstamp", "binance"]:
                val = entry_tick.get(feed, 0)
                if val and val > 0:
                    feeds.append(val)

            if not feeds:
                continue

            avg_exchange = sum(feeds) / len(feeds)
            strike = entry_tick["strike"]
            signal = avg_exchange - strike

            # Trade direction based on signal
            if signal > 0:
                side = "yes"
                entry_price = entry_tick["yes_ask"]
            elif signal < 0:
                side = "no"
                entry_price = 100 - entry_tick["yes_bid"]
            else:
                continue

            if entry_price <= 0 or entry_price >= 100:
                continue

            winner = find_settlement(ticks)
            if not winner:
                continue

            if side == winner:
                pnl = 100 - entry_price
            else:
                pnl = -entry_price

            trades.append({
                "ticker": ticker,
                "side": side,
                "entry_price": entry_price,
                "signal": signal,
                "winner": winner,
                "pnl": pnl,
                "num_feeds": len(feeds),
            })

        if trades:
            total_pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            win_rate = 100 * len(wins) / len(trades)
            avg_pnl = total_pnl / len(trades)

            result = {
                "strategy": "C1", "mins": mins, "trades": len(trades),
                "wins": len(wins), "win_rate": win_rate,
                "total_pnl": total_pnl, "avg_pnl": avg_pnl,
            }
            all_results.append(result)

            if avg_pnl > best_avg_pnl and len(trades) >= 5:
                best_avg_pnl = avg_pnl
                best_config = f"C1 @ {mins}min"

            print(f"{mins:>4} {len(trades):>6} {len(wins):>5} "
                  f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c")

    # C2: Exchange signal DISAGREES with Kalshi pricing
    print()
    print("--- C2: EXCHANGE DISAGREES WITH KALSHI ---")
    print("(Exchange says YES but Kalshi prices YES cheap, or vice versa)")
    print(f"{'Mins':>4} {'MaxKP':>5} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
          f"{'TotPnL':>8} {'AvgPnL':>7}")
    print("-" * 60)

    kalshi_thresholds = [40, 45, 50, 55]  # Kalshi prices below this = "cheap"

    for mins in entry_mins:
        target_secs = SETTLEMENT_DELAY + (mins * 60)

        for kalshi_max in kalshi_thresholds:
            trades = []

            for ticker, ticks in markets.items():
                entry_tick = find_tick_at_time(ticks, target_secs)
                if not entry_tick:
                    continue

                feeds = []
                for feed in ["coinbase", "kraken", "bitstamp", "binance"]:
                    val = entry_tick.get(feed, 0)
                    if val and val > 0:
                        feeds.append(val)
                if not feeds:
                    continue

                avg_exchange = sum(feeds) / len(feeds)
                strike = entry_tick["strike"]
                signal = avg_exchange - strike

                yes_ask = entry_tick["yes_ask"]
                no_ask = 100 - entry_tick["yes_bid"]

                # Look for DISAGREEMENT:
                # Exchange says above strike (YES) but Kalshi prices YES cheap
                if signal > 0 and yes_ask <= kalshi_max and yes_ask > 0:
                    side = "yes"
                    entry_price = yes_ask
                # Exchange says below strike (NO) but Kalshi prices NO cheap
                elif signal < 0 and no_ask <= kalshi_max and no_ask > 0:
                    side = "no"
                    entry_price = no_ask
                else:
                    continue

                winner = find_settlement(ticks)
                if not winner:
                    continue

                if side == winner:
                    pnl = 100 - entry_price
                else:
                    pnl = -entry_price

                trades.append({
                    "ticker": ticker,
                    "side": side,
                    "entry_price": entry_price,
                    "signal": signal,
                    "winner": winner,
                    "pnl": pnl,
                })

            if not trades:
                continue

            total_pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            win_rate = 100 * len(wins) / len(trades)
            avg_pnl = total_pnl / len(trades)

            result = {
                "strategy": "C2", "mins": mins, "kalshi_max": kalshi_max,
                "trades": len(trades), "wins": len(wins),
                "win_rate": win_rate, "total_pnl": total_pnl, "avg_pnl": avg_pnl,
            }
            all_results.append(result)

            if avg_pnl > best_avg_pnl and len(trades) >= 5:
                best_avg_pnl = avg_pnl
                best_config = f"C2 @ {mins}min, Kalshi max {kalshi_max}c"

            print(f"{mins:>4} {kalshi_max:>5} {len(trades):>6} {len(wins):>5} "
                  f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c")

    # C3: Signal STRENGTH - only trade when exchange signal is strong
    print()
    print("--- C3: STRONG EXCHANGE SIGNAL (signal strength filter) ---")
    print(f"{'Mins':>4} {'MinSig':>6} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
          f"{'TotPnL':>8} {'AvgPnL':>7}")
    print("-" * 55)

    signal_thresholds = [5, 10, 15, 20, 30, 50, 75, 100]

    for mins in entry_mins:
        target_secs = SETTLEMENT_DELAY + (mins * 60)

        for min_signal in signal_thresholds:
            trades = []

            for ticker, ticks in markets.items():
                entry_tick = find_tick_at_time(ticks, target_secs)
                if not entry_tick:
                    continue

                feeds = []
                for feed in ["coinbase", "kraken", "bitstamp", "binance"]:
                    val = entry_tick.get(feed, 0)
                    if val and val > 0:
                        feeds.append(val)
                if not feeds:
                    continue

                avg_exchange = sum(feeds) / len(feeds)
                strike = entry_tick["strike"]
                signal = avg_exchange - strike

                # Only trade when signal is strong enough
                if abs(signal) < min_signal:
                    continue

                if signal > 0:
                    side = "yes"
                    entry_price = entry_tick["yes_ask"]
                else:
                    side = "no"
                    entry_price = 100 - entry_tick["yes_bid"]

                if entry_price <= 0 or entry_price >= 100:
                    continue

                winner = find_settlement(ticks)
                if not winner:
                    continue

                if side == winner:
                    pnl = 100 - entry_price
                else:
                    pnl = -entry_price

                trades.append({
                    "ticker": ticker,
                    "side": side,
                    "entry_price": entry_price,
                    "signal": signal,
                    "winner": winner,
                    "pnl": pnl,
                })

            if not trades:
                continue

            total_pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            win_rate = 100 * len(wins) / len(trades)
            avg_pnl = total_pnl / len(trades)

            result = {
                "strategy": "C3", "mins": mins, "min_signal": min_signal,
                "trades": len(trades), "wins": len(wins),
                "win_rate": win_rate, "total_pnl": total_pnl, "avg_pnl": avg_pnl,
            }
            all_results.append(result)

            if avg_pnl > best_avg_pnl and len(trades) >= 5:
                best_avg_pnl = avg_pnl
                best_config = f"C3 @ {mins}min, signal >= {min_signal}"

            print(f"{mins:>4} {min_signal:>6} {len(trades):>6} {len(wins):>5} "
                  f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c")

    # C4: ALL feeds agree on direction vs strike
    print()
    print("--- C4: ALL FEEDS UNANIMOUS (all exchanges agree vs strike) ---")
    print(f"{'Mins':>4} {'Trades':>6} {'Wins':>5} {'WinR%':>6} "
          f"{'TotPnL':>8} {'AvgPnL':>7}")
    print("-" * 50)

    for mins in entry_mins:
        target_secs = SETTLEMENT_DELAY + (mins * 60)
        trades = []

        for ticker, ticks in markets.items():
            entry_tick = find_tick_at_time(ticks, target_secs)
            if not entry_tick:
                continue

            strike = entry_tick["strike"]
            feed_vals = {}
            for feed in ["coinbase", "kraken", "bitstamp", "binance"]:
                val = entry_tick.get(feed, 0)
                if val and val > 0:
                    feed_vals[feed] = val

            if len(feed_vals) < 3:  # Need at least 3 feeds
                continue

            # Check if ALL feeds agree on direction
            above = sum(1 for v in feed_vals.values() if v > strike)
            below = sum(1 for v in feed_vals.values() if v < strike)

            if above == len(feed_vals):
                side = "yes"
                entry_price = entry_tick["yes_ask"]
            elif below == len(feed_vals):
                side = "no"
                entry_price = 100 - entry_tick["yes_bid"]
            else:
                continue  # Not unanimous

            if entry_price <= 0 or entry_price >= 100:
                continue

            winner = find_settlement(ticks)
            if not winner:
                continue

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
            })

        if trades:
            total_pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            win_rate = 100 * len(wins) / len(trades)
            avg_pnl = total_pnl / len(trades)

            result = {
                "strategy": "C4", "mins": mins, "trades": len(trades),
                "wins": len(wins), "win_rate": win_rate,
                "total_pnl": total_pnl, "avg_pnl": avg_pnl,
            }
            all_results.append(result)

            if avg_pnl > best_avg_pnl and len(trades) >= 5:
                best_avg_pnl = avg_pnl
                best_config = f"C4 @ {mins}min (unanimous feeds)"

            print(f"{mins:>4} {len(trades):>6} {len(wins):>5} "
                  f"{win_rate:>5.1f}% {total_pnl:>7.1f}c {avg_pnl:>6.1f}c")

    print()
    if best_config:
        print(f"  BEST STRATEGY C CONFIG (min 5 trades): {best_config} -> avg P&L {best_avg_pnl:.1f}c/trade")
    print()

    return all_results


# =============================================================================
# MAIN
# =============================================================================

def main():
    files = [f for f in sys.argv[1:] if not f.startswith("--")]
    if not files:
        print("Usage: python3 backtest_strategies.py data/*.jsonl")
        sys.exit(1)

    print(f"Loading data from {len(files)} files...")
    markets = load_data(files)
    print(f"Loaded {len(markets)} unique markets")

    # Count settlements
    settled = 0
    for ticker, ticks in markets.items():
        if find_settlement(ticks):
            settled += 1
    print(f"Markets with settlement data: {settled}/{len(markets)}")
    print()

    # Run all three strategies
    results_a = strategy_a(markets)
    results_b = strategy_b(markets)
    results_c = strategy_c(markets)

    # ==========================================================================
    # OVERALL COMPARISON
    # ==========================================================================
    print("=" * 80)
    print("OVERALL COMPARISON: BEST CONFIGS FROM EACH STRATEGY")
    print("=" * 80)
    print()

    # Find best from each
    all_configs = []

    if results_a:
        best_a = max([r for r in results_a if r["trades"] >= 5],
                     key=lambda r: r["avg_pnl"], default=None)
        if best_a:
            all_configs.append({
                "name": f"A: Contrarian {best_a['mins']}min max{best_a['max_price']}c",
                "trades": best_a["trades"], "win_rate": best_a["win_rate"],
                "avg_pnl": best_a["avg_pnl"], "total_pnl": best_a["total_pnl"],
            })

    if results_b:
        best_b = max([r for r in results_b if r["trades"] >= 5],
                     key=lambda r: r["avg_pnl"], default=None)
        if best_b:
            all_configs.append({
                "name": f"B: Momentum {best_b['entry_min']}min {best_b['lookback']}s lb {best_b['threshold']}c thr",
                "trades": best_b["trades"], "win_rate": best_b["win_rate"],
                "avg_pnl": best_b["avg_pnl"], "total_pnl": best_b["total_pnl"],
            })

    if results_c:
        best_c = max([r for r in results_c if r["trades"] >= 5],
                     key=lambda r: r["avg_pnl"], default=None)
        if best_c:
            label = best_c.get("strategy", "C?")
            detail = f"{best_c['mins']}min"
            if "min_signal" in best_c:
                detail += f" sig>={best_c['min_signal']}"
            if "kalshi_max" in best_c:
                detail += f" kmax={best_c['kalshi_max']}"
            all_configs.append({
                "name": f"C: Exchange {label} @ {detail}",
                "trades": best_c["trades"], "win_rate": best_c["win_rate"],
                "avg_pnl": best_c["avg_pnl"], "total_pnl": best_c["total_pnl"],
            })

    if all_configs:
        all_configs.sort(key=lambda c: c["avg_pnl"], reverse=True)
        print(f"{'Strategy':<55} {'Trades':>6} {'WinR%':>6} {'AvgPnL':>7} {'TotPnL':>8}")
        print("-" * 85)
        for c in all_configs:
            marker = " ***" if c == all_configs[0] else ""
            print(f"{c['name']:<55} {c['trades']:>6} {c['win_rate']:>5.1f}% "
                  f"{c['avg_pnl']:>6.1f}c {c['total_pnl']:>7.1f}c{marker}")
        print()
        print(f"  WINNER: {all_configs[0]['name']}")
        print(f"          Avg P&L: {all_configs[0]['avg_pnl']:.1f}c/trade over {all_configs[0]['trades']} trades")
    else:
        print("  No strategies produced enough trades for comparison.")

    print()


if __name__ == "__main__":
    main()
