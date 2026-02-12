package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strconv"

	"github.com/gw/btc15m-data/internal/config"
	"github.com/gw/btc15m-data/internal/kalshi"
	"github.com/gw/btc15m-data/internal/tradelog"
)

const dbPath = "data/tradelog.db"

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})))

	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	cmd := os.Args[1]

	switch cmd {
	case "sync":
		runSync()
	case "pnl":
		runPnL()
	case "positions":
		runPositions(false)
	case "open":
		runPositions(true)
	case "trades":
		limit := 50
		if len(os.Args) > 2 {
			if n, err := strconv.Atoi(os.Args[2]); err == nil {
				limit = n
			}
		}
		runTrades(limit)
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		usage()
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `Usage: tradelog <command>

Commands:
  sync          Fetch all data from Kalshi API
  pnl           Show daily PnL table
  positions     Show all positions with settlement status
  open          Show open (unsettled) positions only
  trades [N]    Show last N fills (default 50)`)
}

func openStore() *tradelog.Store {
	store, err := tradelog.Open(dbPath)
	if err != nil {
		slog.Error("opening db", "err", err)
		os.Exit(1)
	}
	return store
}

func runSync() {
	cfg, err := config.Load()
	if err != nil {
		slog.Error("config error", "err", err)
		os.Exit(1)
	}

	client, err := kalshi.NewClient(cfg)
	if err != nil {
		slog.Error("kalshi client init", "err", err)
		os.Exit(1)
	}

	store := openStore()
	defer store.Close()

	ctx := context.Background()
	if err := tradelog.Sync(ctx, client, store); err != nil {
		slog.Error("sync failed", "err", err)
		os.Exit(1)
	}

	fmt.Println("Sync complete.")
}

func runPnL() {
	store := openStore()
	defer store.Close()

	rows, err := store.GetDailyPnL(context.Background())
	if err != nil {
		slog.Error("query failed", "err", err)
		os.Exit(1)
	}

	if len(rows) == 0 {
		fmt.Println("No PnL data. Run 'tradelog sync' first.")
		return
	}

	fmt.Printf("%-12s %10s %10s %10s %6s\n", "Date", "Revenue", "Cost", "Net PnL", "Trades")
	fmt.Println("--------------------------------------------------------------")
	var totalRev, totalCost, totalPnL, totalTrades int
	for _, r := range rows {
		fmt.Printf("%-12s %10s %10s %10s %6d\n",
			r.Date,
			cents(r.Revenue),
			cents(r.Cost),
			cents(r.NetPnL),
			r.Trades,
		)
		totalRev += r.Revenue
		totalCost += r.Cost
		totalPnL += r.NetPnL
		totalTrades += r.Trades
	}
	fmt.Println("--------------------------------------------------------------")
	fmt.Printf("%-12s %10s %10s %10s %6d\n", "TOTAL", cents(totalRev), cents(totalCost), cents(totalPnL), totalTrades)
}

func runPositions(openOnly bool) {
	store := openStore()
	defer store.Close()

	ctx := context.Background()
	var rows []tradelog.Position
	var err error
	if openOnly {
		rows, err = store.OpenPositions(ctx)
	} else {
		rows, err = store.GetPositions(ctx)
	}
	if err != nil {
		slog.Error("query failed", "err", err)
		os.Exit(1)
	}

	if len(rows) == 0 {
		if openOnly {
			fmt.Println("No open positions.")
		} else {
			fmt.Println("No positions. Run 'tradelog sync' first.")
		}
		return
	}

	fmt.Printf("%-35s %5s %5s %10s %10s %8s %10s\n",
		"Ticker", "Yes", "No", "YesCost", "NoCost", "Result", "Revenue")
	fmt.Println("---------------------------------------------------------------------------------------------------")
	for _, p := range rows {
		fmt.Printf("%-35s %5d %5d %10s %10s %8s %10s\n",
			p.Ticker,
			p.YesContracts,
			p.NoContracts,
			cents(p.YesCost),
			cents(p.NoCost),
			p.MarketResult,
			cents(p.Revenue),
		)
	}
}

func runTrades(limit int) {
	store := openStore()
	defer store.Close()

	fills, err := store.RecentTrades(context.Background(), limit)
	if err != nil {
		slog.Error("query failed", "err", err)
		os.Exit(1)
	}

	if len(fills) == 0 {
		fmt.Println("No trades. Run 'tradelog sync' first.")
		return
	}

	fmt.Printf("%-20s %-35s %5s %5s %5s %5s %5s\n",
		"Time", "Ticker", "Side", "Act", "Price", "Qty", "Taker")
	fmt.Println("---------------------------------------------------------------------------------------------------")
	for _, f := range fills {
		price := f.YesPrice
		if f.Side == "no" {
			price = f.NoPrice
		}
		taker := " "
		if f.IsTaker {
			taker = "Y"
		}
		fmt.Printf("%-20s %-35s %5s %5s %5d %5d %5s\n",
			f.CreatedTime.Format("2006-01-02 15:04:05"),
			f.Ticker,
			f.Side,
			f.Action,
			price,
			f.Count,
			taker,
		)
	}
}

func cents(c int) string {
	sign := ""
	if c < 0 {
		sign = "-"
		c = -c
	}
	return fmt.Sprintf("%s$%d.%02d", sign, c/100, c%100)
}
