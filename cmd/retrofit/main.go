package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/gw/btc15m-data/internal/config"
	"github.com/gw/btc15m-data/internal/kalshi"
)

// TickRecord mirrors the structure in internal/collector/collector.go
type TickRecord struct {
	Type     string       `json:"type"`
	Ts       string       `json:"ts"`
	BRTI     float64      `json:"brti"`
	Coinbase float64      `json:"coinbase"`
	Kraken   float64      `json:"kraken"`
	Bitstamp float64      `json:"bitstamp"`
	Binance  float64      `json:"binance"`
	Markets  []MarketSnap `json:"markets,omitempty"`
}

type MarketSnap struct {
	Ticker    string  `json:"ticker"`
	YesBid    int     `json:"yes_bid"`
	YesAsk    int     `json:"yes_ask"`
	LastPrice int     `json:"last_price"`
	Volume    int     `json:"volume"`
	OpenInt   int     `json:"open_interest"`
	Strike    float64 `json:"strike,omitempty"`
	SecsLeft  int     `json:"secs_left"`
	Status    string  `json:"status,omitempty"`
	Result    string  `json:"result,omitempty"`
}

type MarketTracker struct {
	Ticker      string
	FirstSeen   time.Time
	LastSeen    time.Time
	MinSecsLeft int
	Expiry      time.Time
	NeedsFetch  bool
}

var (
	dryRun          = flag.Bool("dry-run", false, "Preview changes without writing")
	settlementDelay = flag.Int("delay", 5, "Minutes to wait after expiry before fetching settlement")
)

func main() {
	flag.Parse()

	if flag.NArg() == 0 {
		log.Fatal("Usage: retrofit [--dry-run] [--delay=5] <jsonl-file-paths...>")
	}

	// Load config for Kalshi client
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("Loading config: %v", err)
	}

	client, err := kalshi.NewClient(cfg)
	if err != nil {
		log.Fatalf("Creating Kalshi client: %v", err)
	}

	// Process each file
	for _, pattern := range flag.Args() {
		matches, err := filepath.Glob(pattern)
		if err != nil {
			log.Printf("Error expanding pattern %s: %v", pattern, err)
			continue
		}

		for _, filePath := range matches {
			if err := processFile(client, filePath); err != nil {
				log.Printf("Error processing %s: %v", filePath, err)
			}
		}
	}
}

func processFile(client *kalshi.Client, filePath string) error {
	log.Printf("Scanning %s...", filePath)

	// Step 1: Scan file and build market tracker + record list
	records, markets, err := scanFile(filePath)
	if err != nil {
		return fmt.Errorf("scanning file: %w", err)
	}

	log.Printf("  Found %d records, %d unique markets", len(records), len(markets))

	// Step 2: Identify expired markets needing settlement
	now := time.Now()
	delay := time.Duration(*settlementDelay) * time.Minute
	var needsFetch []string

	for ticker, tracker := range markets {
		// Check if market has settlement info already
		hasSettlement := false
		for _, rec := range records {
			for _, snap := range rec.Markets {
				if snap.Ticker == ticker && snap.Status != "" {
					hasSettlement = true
					break
				}
			}
			if hasSettlement {
				break
			}
		}

		if hasSettlement {
			continue
		}

		// Check if expired + delay has passed
		if now.After(tracker.Expiry.Add(delay)) {
			needsFetch = append(needsFetch, ticker)
		}
	}

	if len(needsFetch) == 0 {
		log.Printf("  No markets need settlement data")
		return nil
	}

	sort.Strings(needsFetch)
	log.Printf("  Identified %d expired markets needing settlement", len(needsFetch))

	if *dryRun {
		log.Printf("  [DRY RUN] Would fetch: %v", needsFetch)
		return nil
	}

	// Step 3: Fetch settlements from Kalshi API
	settlements := make(map[string]*kalshi.Market)
	ctx := context.Background()

	log.Printf("Fetching settlements from Kalshi API...")
	for i, ticker := range needsFetch {
		log.Printf("  [%d/%d] %s...", i+1, len(needsFetch), ticker)

		market, err := client.GetMarket(ctx, ticker)
		if err != nil {
			log.Printf("    ERROR: %v", err)
			continue
		}

		settlements[ticker] = market
		log.Printf("    status=%s, result=%s", market.Status, market.Result)

		// Rate limit: 1 request per second
		if i < len(needsFetch)-1 {
			time.Sleep(1 * time.Second)
		}
	}

	if len(settlements) == 0 {
		log.Printf("  No settlements fetched")
		return nil
	}

	// Step 4: Update records in memory
	log.Printf("Updating records...")
	updatedCount := 0
	for i := range records {
		for j := range records[i].Markets {
			snap := &records[i].Markets[j]
			if settlement, ok := settlements[snap.Ticker]; ok {
				snap.Status = settlement.Status
				snap.Result = settlement.Result
				updatedCount++
			}
		}
	}

	log.Printf("  Updated %d market snapshots across %d settlements", updatedCount, len(settlements))

	// Step 5: Write file with backup
	backupPath := filePath + ".pre-retrofit.jsonl"
	if err := copyFile(filePath, backupPath); err != nil {
		return fmt.Errorf("creating backup: %w", err)
	}
	log.Printf("  Backup: %s", backupPath)

	if err := writeRecords(filePath, records); err != nil {
		return fmt.Errorf("writing updated file: %w", err)
	}

	log.Printf("Done! Retrofitted %d markets in %s", len(settlements), filePath)
	return nil
}

func scanFile(filePath string) ([]TickRecord, map[string]*MarketTracker, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()

	var records []TickRecord
	markets := make(map[string]*MarketTracker)

	scanner := bufio.NewScanner(f)
	lineNum := 0

	for scanner.Scan() {
		lineNum++
		line := scanner.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}

		var rec TickRecord
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			return nil, nil, fmt.Errorf("line %d: %w", lineNum, err)
		}

		records = append(records, rec)

		// Parse timestamp
		ts, err := time.Parse(time.RFC3339Nano, rec.Ts)
		if err != nil {
			// Try RFC3339 without nano
			ts, err = time.Parse(time.RFC3339, rec.Ts)
			if err != nil {
				continue
			}
		}

		// Track markets
		for _, snap := range rec.Markets {
			tracker, exists := markets[snap.Ticker]
			if !exists {
				tracker = &MarketTracker{
					Ticker:      snap.Ticker,
					FirstSeen:   ts,
					LastSeen:    ts,
					MinSecsLeft: snap.SecsLeft,
				}
				markets[snap.Ticker] = tracker
			}

			if ts.After(tracker.LastSeen) {
				tracker.LastSeen = ts
				tracker.MinSecsLeft = snap.SecsLeft
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, nil, err
	}

	// Calculate expiry times
	for _, tracker := range markets {
		tracker.Expiry = tracker.LastSeen.Add(time.Duration(tracker.MinSecsLeft) * time.Second)
	}

	return records, markets, nil
}

func writeRecords(filePath string, records []TickRecord) error {
	tmpPath := filePath + ".tmp"

	f, err := os.Create(tmpPath)
	if err != nil {
		return err
	}

	encoder := json.NewEncoder(f)
	for _, rec := range records {
		if err := encoder.Encode(rec); err != nil {
			f.Close()
			os.Remove(tmpPath)
			return err
		}
	}

	if err := f.Close(); err != nil {
		os.Remove(tmpPath)
		return err
	}

	// Atomic rename
	return os.Rename(tmpPath, filePath)
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0644)
}
