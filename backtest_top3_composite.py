#!/usr/bin/env python3
"""
Backtest: Top 3 Candidate Strategies + Composite

Tests the three best candidate strategies identified from prior analysis,
then runs a composite strategy that combines signals from all three.

CRITICAL TIMING:
  secs_left counts to SETTLEMENT, not market close.
  Market close ~ 294 seconds before settlement.
  target_secs_left = 294 + (mins_before_close * 60)
  ONLY use prices where secs_left > 294

Usage: python3 backtest_top3_composite.py data/*.jsonl
"""

import json
import sys
from collections import defaultdict


SETTLEMENT_DELAY = 294  # secs_left when market closes


# =============================================================================
# DATA LOADING
# =============================================================================

def load_data(files):
    """Load all JSONL files, group ticks by market ticker."""
    markets = defaultdict(list)
    tick_count = 0
    file_count = 0

    for filepath in files:
        file_count += 1
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if tick.get("type") != "tick":
                    continue

                tick_count += 1
                brti = tick.get("brti", 0)
                if brti == 0:
                    continue

                ts = tick["ts"]
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
                        "yes_bid": market.get("yes_bid", 0),
                        "yes_ask": market.get("yes_ask", 0),
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

    print(f"Loaded {file_count} files, {tick_count} ticks, {len(markets)} unique markets")
    return markets


def find_settlement(ticks):
    """Search backwards for settlement result. Returns 'yes' or 'no' or None."""
    for tick in reversed(ticks):
        result = tick.get("result", "")
        if result:
            return result.lower()
        status = tick.get("status", "")
        if status in ("finalized", "determined"):
            if tick["brti"] >= tick["strike"]:
                return "yes"
            else:
                return "no"

    # Fallback: use last active tick's BRTI vs strike
    active_ticks = [t for t in ticks if t["secs_left"] > SETTLEMENT_DELAY]
    if active_ticks:
        lt = active_ticks[-1]
        if lt["brti"] >= lt["strike"]:
            return "yes"
        else:
            return "no"
    return None


def find_tick_at_target(ticks, target_secs_left, tolerance=30):
    """Find the tick closest to target_secs_left. Must be > SETTLEMENT_DELAY."""
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
    return None


def prepare_markets(raw_markets):
    """Sort ticks and resolve settlement for each market."""
    prepared = {}
    settled = 0
    unsettled = 0
    for ticker, ticks in raw_markets.items():
        ticks.sort(key=lambda t: t["ts"])
        settlement = find_settlement(ticks)
        if settlement is None:
            unsettled += 1
            continue
        settled += 1
        prepared[ticker] = {
            "ticks": ticks,
            "settlement": settlement,
        }
    print(f"Markets: {settled} settled, {unsettled} unsettled (skipped)")
    return prepared


# =============================================================================
# METRICS
# =============================================================================

def compute_metrics(trades):
    """Compute standard metrics for a list of trades."""
    if not trades:
        return {
            "num_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_pnl": 0, "max_drawdown": 0,
        }
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / len(trades)

    # Maximum drawdown: worst cumulative loss during the sequence
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["pnl"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        "num_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": 100 * len(wins) / len(trades),
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "max_drawdown": max_dd,
    }


def print_strategy_results(name, trades, metrics):
    """Print full results for a strategy."""
    print(f"\n{'=' * 90}")
    print(f"  {name}")
    print(f"{'=' * 90}")

    if metrics["num_trades"] == 0:
        print("  No trades executed.\n")
        return

    print(f"  Trades: {metrics['num_trades']}  |  "
          f"Wins: {metrics['wins']}  |  Losses: {metrics['losses']}  |  "
          f"Win Rate: {metrics['win_rate']:.1f}%")
    print(f"  Total P&L: {metrics['total_pnl']:+.0f}c (${metrics['total_pnl']/100:+.2f})  |  "
          f"Avg P&L/trade: {metrics['avg_pnl']:+.1f}c  |  "
          f"Max Drawdown: {metrics['max_drawdown']:.0f}c (${metrics['max_drawdown']/100:.2f})")
    print()

    # Individual trades
    print(f"  {'#':>4} {'Ticker':<38} {'Side':>4} {'Entry':>6} {'Result':>6} {'P&L':>7} {'CumPnL':>8}  Details")
    print(f"  {'-'*4} {'-'*38} {'-'*4} {'-'*6} {'-'*6} {'-'*7} {'-'*8}  {'-'*30}")

    cumulative = 0
    for i, t in enumerate(trades, 1):
        cumulative += t["pnl"]
        result_str = "WIN" if t["pnl"] > 0 else "LOSS"
        details = t.get("details", "")
        print(f"  {i:>4} {t['ticker']:<38} {t['side']:>4} {t['entry_price']:>5.0f}c "
              f"{result_str:>6} {t['pnl']:>+6.0f}c {cumulative:>+7.0f}c  {details}")

    print()


# =============================================================================
# STRATEGY 1: Strong Distance (7min, $75+ from strike)
# =============================================================================

def strategy_strong_distance(prepared):
    """At 7 min before close, if abs(brti - strike) >= $75, trade the direction."""
    MINS_BEFORE = 7
    DISTANCE_THRESHOLD = 75  # dollars
    target_secs = SETTLEMENT_DELAY + (MINS_BEFORE * 60)  # 294 + 420 = 714

    trades = []
    for ticker, data in sorted(prepared.items()):
        ticks = data["ticks"]
        settlement = data["settlement"]

        entry = find_tick_at_target(ticks, target_secs)
        if entry is None:
            continue

        brti = entry["brti"]
        strike = entry["strike"]
        distance = brti - strike

        if abs(distance) < DISTANCE_THRESHOLD:
            continue

        yes_ask = entry["yes_ask"]
        yes_bid = entry["yes_bid"]
        no_ask = 100 - yes_bid

        if distance > 0:
            side = "yes"
            entry_price = yes_ask
        else:
            side = "no"
            entry_price = no_ask

        if entry_price <= 0 or entry_price >= 100:
            continue

        if side == settlement:
            pnl = 100 - entry_price
        else:
            pnl = -entry_price

        trades.append({
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "pnl": pnl,
            "details": f"dist=${distance:+.0f} brti={brti:.0f} strike={strike:.0f} secs={entry['secs_left']}",
        })

    return trades


# =============================================================================
# STRATEGY 2: High Confidence Late (4min, >=85c)
# =============================================================================

def strategy_high_confidence_late(prepared):
    """At 4 min before close, buy whichever side is >= 85c."""
    MINS_BEFORE = 4
    MIN_PRICE = 85
    target_secs = SETTLEMENT_DELAY + (MINS_BEFORE * 60)  # 294 + 240 = 534

    trades = []
    for ticker, data in sorted(prepared.items()):
        ticks = data["ticks"]
        settlement = data["settlement"]

        entry = find_tick_at_target(ticks, target_secs)
        if entry is None:
            continue

        yes_ask = entry["yes_ask"]
        yes_bid = entry["yes_bid"]
        no_ask = 100 - yes_bid

        side = None
        entry_price = None

        if yes_ask >= MIN_PRICE:
            side = "yes"
            entry_price = yes_ask
        elif no_ask >= MIN_PRICE:
            side = "no"
            entry_price = no_ask
        else:
            continue

        if entry_price <= 0 or entry_price >= 100:
            continue

        if side == settlement:
            pnl = 100 - entry_price
        else:
            pnl = -entry_price

        trades.append({
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "pnl": pnl,
            "details": f"yes_ask={yes_ask} no_ask={no_ask} secs={entry['secs_left']}",
        })

    return trades


# =============================================================================
# STRATEGY 3: Momentum Burst (7min, 30s lookback, 8c move, min 55c)
# =============================================================================

def strategy_momentum_burst(prepared):
    """At 7 min before close, check if yes_ask moved 8c+ in last 30s.
    Buy the direction of the move if the side is >= 55c."""
    MINS_BEFORE = 7
    LOOKBACK_SECS = 30
    THRESHOLD = 8  # cents
    MIN_CONFIDENCE = 55  # cents
    target_secs = SETTLEMENT_DELAY + (MINS_BEFORE * 60)  # 714

    trades = []
    for ticker, data in sorted(prepared.items()):
        ticks = data["ticks"]
        settlement = data["settlement"]

        # Find current tick at entry time
        now_tick = find_tick_at_target(ticks, target_secs)
        if now_tick is None:
            continue

        # Find earlier tick (~30 seconds earlier = higher secs_left)
        earlier_secs = now_tick["secs_left"] + LOOKBACK_SECS
        earlier_tick = find_tick_at_target(ticks, earlier_secs, tolerance=15)
        if earlier_tick is None:
            continue

        yes_ask_now = now_tick["yes_ask"]
        yes_ask_before = earlier_tick["yes_ask"]

        if yes_ask_now == 0 or yes_ask_before == 0:
            continue

        price_change = yes_ask_now - yes_ask_before

        side = None
        entry_price = None
        no_ask_now = 100 - now_tick["yes_bid"]

        if price_change >= THRESHOLD:
            # YES price surging -> buy YES
            if yes_ask_now >= MIN_CONFIDENCE:
                side = "yes"
                entry_price = yes_ask_now
        elif price_change <= -THRESHOLD:
            # YES price dropping -> NO is surging -> buy NO
            if no_ask_now >= MIN_CONFIDENCE:
                side = "no"
                entry_price = no_ask_now

        if side is None:
            continue

        if entry_price <= 0 or entry_price >= 100:
            continue

        if side == settlement:
            pnl = 100 - entry_price
        else:
            pnl = -entry_price

        trades.append({
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "pnl": pnl,
            "details": f"chg={price_change:+.0f}c now={yes_ask_now} before={yes_ask_before} secs={now_tick['secs_left']}",
        })

    return trades


# =============================================================================
# STRATEGY 4: Composite (combines signals, scored)
# =============================================================================

def strategy_composite(prepared):
    """At 7 min before close, score each market on multiple signals.
    Trade if score >= threshold. Returns trades for each threshold."""
    MINS_BEFORE = 7
    LOOKBACK_SECS = 30
    DISTANCE_THRESHOLD = 75
    MOMENTUM_THRESHOLD = 8
    PRICE_CONFIDENCE = 75
    target_secs = SETTLEMENT_DELAY + (MINS_BEFORE * 60)  # 714

    all_scored = []  # list of (ticker, score, direction, entry_price, settlement, details)

    for ticker, data in sorted(prepared.items()):
        ticks = data["ticks"]
        settlement = data["settlement"]

        now_tick = find_tick_at_target(ticks, target_secs)
        if now_tick is None:
            continue

        brti = now_tick["brti"]
        strike = now_tick["strike"]
        distance = brti - strike

        yes_ask = now_tick["yes_ask"]
        yes_bid = now_tick["yes_bid"]
        no_ask = 100 - yes_bid

        if yes_ask <= 0 or yes_bid <= 0:
            continue

        score = 0
        signal_details = []

        # Signal 1: Distance >= $75
        if abs(distance) >= DISTANCE_THRESHOLD:
            score += 1
            signal_details.append(f"dist=${distance:+.0f}")

        # Signal 2: High confidence price (max side >= 75c)
        max_side_price = max(yes_ask, no_ask)
        if max_side_price >= PRICE_CONFIDENCE:
            score += 1
            signal_details.append(f"maxP={max_side_price}c")

        # Signal 3: Momentum agrees with direction
        earlier_secs = now_tick["secs_left"] + LOOKBACK_SECS
        earlier_tick = find_tick_at_target(ticks, earlier_secs, tolerance=15)
        momentum_direction = None
        if earlier_tick and earlier_tick["yes_ask"] > 0 and yes_ask > 0:
            price_change = yes_ask - earlier_tick["yes_ask"]
            if price_change >= MOMENTUM_THRESHOLD:
                momentum_direction = "yes"
            elif price_change <= -MOMENTUM_THRESHOLD:
                momentum_direction = "no"

            # Does momentum agree with distance direction?
            brti_direction = "yes" if distance > 0 else "no"
            if momentum_direction and momentum_direction == brti_direction:
                score += 1
                signal_details.append(f"mom={price_change:+.0f}c")

        # Signal 4: All exchange feeds agree on direction vs strike
        feed_vals = {}
        for feed_name in ("coinbase", "kraken", "bitstamp", "binance"):
            val = now_tick.get(feed_name, 0)
            if val and val > 0:
                feed_vals[feed_name] = val

        if len(feed_vals) >= 2:  # need at least 2 feeds
            above = sum(1 for v in feed_vals.values() if v > strike)
            below = sum(1 for v in feed_vals.values() if v < strike)
            if above == len(feed_vals) or below == len(feed_vals):
                score += 1
                signal_details.append(f"feeds={len(feed_vals)}agree")

        if score < 2:
            continue

        # Direction: follow BRTI vs strike
        if distance > 0:
            direction = "yes"
            entry_price = yes_ask
        elif distance < 0:
            direction = "no"
            entry_price = no_ask
        else:
            continue

        if entry_price <= 0 or entry_price >= 100:
            continue

        if direction == settlement:
            pnl = 100 - entry_price
        else:
            pnl = -entry_price

        all_scored.append({
            "ticker": ticker,
            "score": score,
            "side": direction,
            "entry_price": entry_price,
            "pnl": pnl,
            "details": f"score={score} [{', '.join(signal_details)}] secs={now_tick['secs_left']}",
        })

    # Bucket by minimum score threshold
    results = {}
    for min_score in (2, 3, 4):
        trades = [s for s in all_scored if s["score"] >= min_score]
        results[min_score] = trades

    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    files = [f for f in sys.argv[1:] if not f.startswith("--")]
    if not files:
        print("Usage: python3 backtest_top3_composite.py data/*.jsonl")
        sys.exit(1)

    print("=" * 90)
    print("  COMPREHENSIVE BACKTEST: Top 3 Strategies + Composite")
    print("=" * 90)
    print()

    # Load data
    raw_markets = load_data(files)
    prepared = prepare_markets(raw_markets)
    print()

    # ── Strategy 1: Strong Distance ──
    trades1 = strategy_strong_distance(prepared)
    metrics1 = compute_metrics(trades1)
    print_strategy_results(
        "STRATEGY 1: Strong Distance (7min, abs(distance) >= $75)", trades1, metrics1
    )

    # ── Strategy 2: High Confidence Late ──
    trades2 = strategy_high_confidence_late(prepared)
    metrics2 = compute_metrics(trades2)
    print_strategy_results(
        "STRATEGY 2: High Confidence Late (4min, side >= 85c)", trades2, metrics2
    )

    # ── Strategy 3: Momentum Burst ──
    trades3 = strategy_momentum_burst(prepared)
    metrics3 = compute_metrics(trades3)
    print_strategy_results(
        "STRATEGY 3: Momentum Burst (7min, 30s lookback, 8c+ move, min 55c)", trades3, metrics3
    )

    # ── Strategy 4: Composite ──
    composite_results = strategy_composite(prepared)

    for min_score in (2, 3, 4):
        trades4 = composite_results[min_score]
        metrics4 = compute_metrics(trades4)
        print_strategy_results(
            f"STRATEGY 4: Composite (score >= {min_score})", trades4, metrics4
        )

    # ── RECOMMENDATION ──
    print()
    print("=" * 90)
    print("  RECOMMENDATION: Strategy Comparison")
    print("=" * 90)
    print()

    all_strategies = [
        ("1: Strong Distance (7m, $75)", metrics1),
        ("2: High Confidence Late (4m, 85c)", metrics2),
        ("3: Momentum Burst (7m, 30s, 8c)", metrics3),
    ]
    for min_score in (2, 3, 4):
        m = compute_metrics(composite_results[min_score])
        all_strategies.append((f"4: Composite (score>={min_score})", m))

    print(f"  {'Strategy':<40} {'Trades':>7} {'WinR%':>7} {'TotPnL':>10} {'$/trade':>8} {'MaxDD':>9}")
    print(f"  {'-'*40} {'-'*7} {'-'*7} {'-'*10} {'-'*8} {'-'*9}")

    best_name = ""
    best_avg = -9999

    for name, m in all_strategies:
        if m["num_trades"] == 0:
            print(f"  {name:<40} {'--':>7}")
            continue

        tot_dollars = m["total_pnl"] / 100
        avg_cents = m["avg_pnl"]
        dd_dollars = m["max_drawdown"] / 100

        marker = ""
        if avg_cents > best_avg and m["num_trades"] >= 5:
            best_avg = avg_cents
            best_name = name

        print(f"  {name:<40} {m['num_trades']:>7} {m['win_rate']:>6.1f}% "
              f"${tot_dollars:>+8.2f} {avg_cents:>+7.1f}c ${dd_dollars:>7.2f}")

    # Mark best
    print()
    if best_name:
        print(f"  >>> BEST STRATEGY: {best_name}")
        for name, m in all_strategies:
            if name == best_name:
                print(f"      Avg P&L: {m['avg_pnl']:+.1f}c/trade over {m['num_trades']} trades")
                print(f"      Win Rate: {m['win_rate']:.1f}%")
                print(f"      Total P&L: {m['total_pnl']:+.0f}c (${m['total_pnl']/100:+.2f})")
                print(f"      Max Drawdown: {m['max_drawdown']:.0f}c (${m['max_drawdown']/100:.2f})")
                break
    else:
        print("  No strategy had enough trades to recommend.")

    print()
    print("=" * 90)
    print("  KEY CONSIDERATIONS")
    print("=" * 90)
    print()
    print("  - All results assume $1 per contract, no fees deducted")
    print("  - Max drawdown shows worst peak-to-trough during the trade sequence")
    print("  - Win rate > 50% is necessary but not sufficient; avg P&L matters more")
    print("  - Fewer trades with higher avg P&L may be better than many marginal trades")
    print("  - Composite strategies benefit from signal confirmation but may trade less")
    print()


if __name__ == "__main__":
    main()
