package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gw/btc15m-data/internal/collector"
	"github.com/gw/btc15m-data/internal/config"
	"github.com/gw/btc15m-data/internal/feed"
	"github.com/gw/btc15m-data/internal/kalshi"
)

func main() {
	output := flag.String("output", "", "output directory for JSONL files")
	series := flag.String("series", "", "series ticker to collect (default KXBTC15M)")
	debug := flag.Bool("debug", false, "enable debug logging")
	flag.Parse()

	// Logging
	logLevel := slog.LevelInfo
	if *debug {
		logLevel = slog.LevelDebug
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel})))

	// Load config
	cfg, err := config.Load()
	if err != nil {
		slog.Error("config error", "err", err)
		os.Exit(1)
	}

	// CLI overrides
	if *output != "" {
		cfg.OutputDir = *output
	}
	if *series != "" {
		cfg.SeriesTicker = *series
	}

	slog.Info("data collector starting",
		"env", cfg.KalshiEnv,
		"series", cfg.SeriesTicker,
		"output", cfg.OutputDir,
	)

	// Init Kalshi client
	client, err := kalshi.NewClient(cfg)
	if err != nil {
		slog.Error("kalshi client init failed", "err", err)
		os.Exit(1)
	}

	// Context with graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigCh
		slog.Info("received signal, shutting down", "signal", sig)
		cancel()
	}()

	// Verify auth with a balance check (retry with backoff for maintenance windows)
	const maxAuthAttempts = 5
	var bal *kalshi.Balance
	for attempt := 1; attempt <= maxAuthAttempts; attempt++ {
		bal, err = client.GetBalance(ctx)
		if err == nil {
			break
		}
		if attempt == maxAuthAttempts {
			slog.Error("auth check failed after retries — giving up", "err", err, "attempts", attempt)
			os.Exit(1)
		}
		backoff := time.Duration(attempt*attempt) * 15 * time.Second // 15s, 60s, 135s, 240s
		slog.Warn("auth check failed, retrying", "err", err, "attempt", attempt, "backoff", backoff)
		select {
		case <-ctx.Done():
			slog.Error("shutdown during auth retry")
			os.Exit(1)
		case <-time.After(backoff):
		}
	}
	slog.Info("authenticated", "balance", fmt.Sprintf("$%.2f", float64(bal.Balance)/100.0))

	// Init price feeds
	coinbase := feed.NewCoinbaseFeed()
	krakenFeed := feed.NewKrakenFeed()
	bitstamp := feed.NewBitstampFeed()

	feeds := []feed.ExchangeFeed{coinbase, krakenFeed, bitstamp}
	brti := feed.NewBRTIProxy(feeds)

	// Start feed goroutines
	for _, f := range feeds {
		f := f
		go func() {
			if err := f.Run(ctx); err != nil && ctx.Err() == nil {
				slog.Error("feed error", "feed", f.Name(), "err", err)
			}
		}()
	}

	// Wait briefly for at least one feed to connect
	slog.Info("waiting for price feeds...")
	waitForFeeds(ctx, feeds)

	price := brti.Snapshot()
	if price > 0 {
		slog.Info("initial BRTI proxy", "price", fmt.Sprintf("$%.2f", price))
	} else {
		slog.Warn("no price feeds connected yet — collector will wait for data")
	}

	// Print feed status
	for _, h := range brti.FeedStatus() {
		status := "connected"
		if h.Stale {
			status = "stale/disconnected"
		}
		slog.Info("feed status", "name", h.Name, "price", fmt.Sprintf("$%.2f", h.Price), "status", status)
	}

	// Create writer
	writer, err := collector.NewWriter(cfg.OutputDir, "kxbtc15m")
	if err != nil {
		slog.Error("writer init failed", "err", err)
		os.Exit(1)
	}
	defer writer.Close()

	// Create and run collector
	c := collector.New(client, brti, feeds, writer, cfg.SeriesTicker)
	if err := c.Run(ctx); err != nil && ctx.Err() == nil {
		slog.Error("collector error", "err", err)
		os.Exit(1)
	}

	slog.Info("collector stopped")
}

func waitForFeeds(ctx context.Context, feeds []feed.ExchangeFeed) {
	deadline := time.After(5 * time.Second)
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-deadline:
			slog.Warn("timed out waiting for feeds")
			return
		case <-tick.C:
			for _, f := range feeds {
				if !f.IsStale() {
					slog.Info("feed connected", "feed", f.Name())
					return
				}
			}
		}
	}
}
