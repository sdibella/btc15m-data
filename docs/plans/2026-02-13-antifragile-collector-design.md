# Antifragile Data Collector — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the data collector self-healing when websocket connections stall, and add an external safety net that restarts the process if it goes silent.

**Architecture:** Internal watchdog goroutine detects write stalls and triggers clean exit (non-zero) for systemd to restart. WS write deadlines prevent blocking. External systemd timer checks file freshness as a belt-and-suspenders fallback.

**Tech Stack:** Go (gorilla/websocket), systemd user services, bash

---

## Task 1: Exchange Feed Dial Timeouts

The simplest, most isolated change. All three exchange feeds use `websocket.DefaultDialer` which has no handshake timeout. A DNS or TLS hang blocks forever.

**Files:**
- Modify: `internal/feed/coinbase.go:53`
- Modify: `internal/feed/kraken.go:49`
- Modify: `internal/feed/bitstamp.go:49`

**Step 1: Fix coinbase dialer**

In `internal/feed/coinbase.go`, replace line 53:

```go
// Before:
conn, _, err := websocket.DefaultDialer.DialContext(ctx, wsURL, nil)

// After:
dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
conn, _, err := dialer.DialContext(ctx, wsURL, nil)
```

**Step 2: Fix kraken dialer**

In `internal/feed/kraken.go`, replace line 49 with the same pattern.

**Step 3: Fix bitstamp dialer**

In `internal/feed/bitstamp.go`, replace line 49 with the same pattern.

**Step 4: Build to verify compilation**

Run: `go build ./...`
Expected: no errors

**Step 5: Commit**

```bash
git add internal/feed/coinbase.go internal/feed/kraken.go internal/feed/bitstamp.go
git commit -m "Add handshake timeout to exchange feed websocket dialers"
```

---

## Task 2: Kalshi WS Write Deadlines

`UpdateSubscriptions` calls `conn.WriteJSON()` three times (lines 481, 496, 518) with no deadline. If the peer is unresponsive, the discovery goroutine blocks forever.

**Files:**
- Modify: `internal/kalshi/ws.go` — `UpdateSubscriptions` method (lines 468-527)

**Step 1: Add write deadlines before each WriteJSON in UpdateSubscriptions**

There are three `WriteJSON` calls in `UpdateSubscriptions`. Before each one, add:

```go
f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
```

And after the block of writes completes (before `f.writeMu.Unlock()` at line 527), clear the deadline:

```go
f.conn.SetWriteDeadline(time.Time{})
```

Also add a write deadline in `subscribeLocked` before the `WriteJSON` at line 422:

```go
f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
if err := f.conn.WriteJSON(cmd); err != nil {
    return err
}
f.conn.SetWriteDeadline(time.Time{}) // clear deadline
```

**Step 2: Build to verify**

Run: `go build ./...`
Expected: no errors

**Step 3: Commit**

```bash
git add internal/kalshi/ws.go
git commit -m "Add write deadlines to Kalshi WS operations"
```

---

## Task 3: Internal Watchdog + Heartbeat

The core resilience change. Add a watchdog goroutine that monitors data flow and kills the process if writes stall.

**Files:**
- Modify: `internal/collector/collector.go`

**Step 1: Add lastWriteTime field and tick counter to Collector struct**

```go
type Collector struct {
	client   *kalshi.Client
	kalshiWS *kalshi.KalshiFeed
	brti     *feed.BRTIProxy
	feeds    []feed.ExchangeFeed
	writer   *Writer
	series   string

	lastWriteMu   sync.Mutex
	lastWriteTime time.Time
	tickCount     int64
}
```

Add `"sync"` to imports.

**Step 2: Update tick() to track successful writes**

Replace the write call at line 183-185:

```go
// Before:
if err := c.writer.Write(rec); err != nil {
    slog.Warn("tick: write failed", "err", err)
}

// After:
if err := c.writer.Write(rec); err != nil {
    slog.Warn("tick: write failed", "err", err)
} else {
    c.lastWriteMu.Lock()
    c.lastWriteTime = time.Now()
    c.tickCount++
    c.lastWriteMu.Unlock()
}
```

**Step 3: Add watchdog goroutine**

Add this method to collector.go:

```go
// watchdog monitors data flow and cancels context if writes stall.
// Also emits a periodic heartbeat log every 60s.
func (c *Collector) watchdog(ctx context.Context, cancel context.CancelFunc) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	heartbeatTicker := time.NewTicker(60 * time.Second)
	defer heartbeatTicker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-heartbeatTicker.C:
			c.lastWriteMu.Lock()
			count := c.tickCount
			lastWrite := c.lastWriteTime
			c.lastWriteMu.Unlock()

			var feedStatus []string
			for _, f := range c.feeds {
				status := "ok"
				if f.IsStale() {
					status = "stale"
				}
				feedStatus = append(feedStatus, f.Name()+":"+status)
			}

			slog.Info("heartbeat",
				"ticks", count,
				"last_write_ago", time.Since(lastWrite).Round(time.Second).String(),
				"feeds", strings.Join(feedStatus, " "),
				"kalshi_ws", c.kalshiWS.IsConnected(),
			)
		case <-ticker.C:
			c.lastWriteMu.Lock()
			lastWrite := c.lastWriteTime
			c.lastWriteMu.Unlock()

			if lastWrite.IsZero() {
				continue // hasn't started writing yet
			}
			if time.Since(lastWrite) > 90*time.Second {
				slog.Error("watchdog: no successful write for 90s, triggering restart",
					"last_write", lastWrite.Format(time.RFC3339),
				)
				cancel()
				return
			}
		}
	}
}
```

Add `"strings"` to imports.

**Step 4: Wire watchdog into Run()**

Modify `Run()` to accept a cancel func and start the watchdog. Change the method to create its own cancellable context:

```go
func (c *Collector) Run(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	// Start watchdog
	go c.watchdog(ctx, cancel)

	// Start market discovery loop
	go c.discoveryLoop(ctx)

	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			c.tick(ctx)
		}
	}
}
```

**Step 5: Build to verify**

Run: `go build ./...`
Expected: no errors

**Step 6: Commit**

```bash
git add internal/collector/collector.go
git commit -m "Add internal watchdog and heartbeat logging to collector"
```

---

## Task 4: Systemd Restart Policy

The current service has `Restart=on-failure`. When the watchdog cancels context, `Run()` returns `context.Canceled` and main exits with code 0 — systemd won't restart it. Fix: either change to `Restart=always` or exit non-zero on watchdog trigger.

**Files:**
- Modify: `datacollector.service:13`
- Modify: `cmd/datacollector/main.go:158-160`

**Step 1: Change systemd restart policy**

In `datacollector.service`, change line 13:

```ini
# Before:
Restart=on-failure

# After:
Restart=always
```

**Step 2: Make watchdog-triggered exit non-zero**

In `cmd/datacollector/main.go`, the existing code at line 158 already handles this:

```go
if err := c.Run(ctx); err != nil && ctx.Err() == nil {
    slog.Error("collector error", "err", err)
    os.Exit(1)
}
```

When the watchdog cancels its own context, `ctx.Err()` returns `context.Canceled`, but the parent context (from signal handler) is still alive. So `c.Run()` returns `context.Canceled`, and `ctx.Err() == nil` is false (because the collector's internal context IS canceled). The process exits cleanly with code 0.

With `Restart=always`, this is fine — systemd restarts regardless of exit code. Add `RestartSec=5` to restart faster than the current 10s.

```ini
Restart=always
RestartSec=5
```

**Step 3: Commit**

```bash
git add datacollector.service
git commit -m "Change systemd restart policy to always for watchdog recovery"
```

---

## Task 5: External Watchdog (systemd timer)

Belt-and-suspenders: a separate systemd timer checks file freshness every 60s and force-restarts the collector if the data file is stale.

**Files:**
- Create: `scripts/watchdog.sh`
- Create: `watchdog.service`
- Create: `watchdog.timer`

**Step 1: Create watchdog script**

Create `scripts/watchdog.sh`:

```bash
#!/usr/bin/env bash
# External watchdog: restart datacollector if data file is stale (>120s old)

set -e

DATA_DIR="/home/stefan/KalshiBTC15min-data/data"
PREFIX="kxbtc15m"
STALE_SECONDS=120
SERVICE="datacollector.service"

TODAY=$(date -u +%Y-%m-%d)
DATA_FILE="$DATA_DIR/${PREFIX}-${TODAY}.jsonl"

# If file doesn't exist, collector may be starting up — skip
if [ ! -f "$DATA_FILE" ]; then
    exit 0
fi

# Check file age
FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$DATA_FILE") ))

if [ "$FILE_AGE" -gt "$STALE_SECONDS" ]; then
    echo "WATCHDOG: data file stale for ${FILE_AGE}s (threshold: ${STALE_SECONDS}s), restarting collector"
    systemctl --user restart "$SERVICE"
    echo "WATCHDOG: collector restarted"
else
    echo "WATCHDOG: ok (file age: ${FILE_AGE}s)"
fi
```

**Step 2: Create watchdog systemd service**

Create `watchdog.service`:

```ini
[Unit]
Description=Data Collector Watchdog
After=datacollector.service

[Service]
Type=oneshot
WorkingDirectory=/home/stefan/KalshiBTC15min-data
ExecStart=/home/stefan/KalshiBTC15min-data/scripts/watchdog.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dc-watchdog
```

**Step 3: Create watchdog timer**

Create `watchdog.timer`:

```ini
[Unit]
Description=Data Collector Watchdog Timer

[Timer]
OnBootSec=120
OnUnitActiveSec=60
AccuracySec=5

[Install]
WantedBy=timers.target
```

**Step 4: Commit**

```bash
chmod +x scripts/watchdog.sh
git add scripts/watchdog.sh watchdog.service watchdog.timer
git commit -m "Add external file-freshness watchdog with systemd timer"
```

---

## Task 6: Deploy and Verify

**Step 1: Push and deploy**

```bash
git push
ssh stefan@tradebot "cd ~/KalshiBTC15min-data && git pull && ./botctl restart"
```

**Step 2: Install watchdog timer on VPS**

```bash
ssh stefan@tradebot "cd ~/KalshiBTC15min-data && \
    mkdir -p scripts && \
    cp watchdog.service watchdog.timer ~/.config/systemd/user/ && \
    cp datacollector.service ~/.config/systemd/user/ && \
    systemctl --user daemon-reload && \
    systemctl --user enable --now watchdog.timer"
```

**Step 3: Verify collector is running with heartbeat**

```bash
ssh stefan@tradebot "sleep 65 && journalctl --user -u datacollector --since '2 min ago' --no-pager | grep heartbeat"
```

Expected: at least one heartbeat log line with ticks count, feed status, and kalshi_ws status.

**Step 4: Verify watchdog timer is active**

```bash
ssh stefan@tradebot "systemctl --user status watchdog.timer --no-pager"
```

Expected: `active (waiting)`, next trigger in ~60s.

**Step 5: Verify watchdog runs clean**

```bash
ssh stefan@tradebot "systemctl --user start watchdog.service && journalctl --user -u dc-watchdog --since '1 min ago' --no-pager"
```

Expected: `WATCHDOG: ok (file age: Ns)` where N < 120.
