package collector

import (
	"context"
	"log/slog"
	"time"

	"github.com/gw/btc15m-data/internal/feed"
	"github.com/gw/btc15m-data/internal/kalshi"
)

// TickRecord is one per-second snapshot of all prices.
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

// MarketSnap is a point-in-time snapshot of a Kalshi market.
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

type Collector struct {
	client *kalshi.Client
	brti   *feed.BRTIProxy
	feeds  []feed.ExchangeFeed
	writer *Writer
	series string
}

func New(client *kalshi.Client, brti *feed.BRTIProxy, feeds []feed.ExchangeFeed, writer *Writer, series string) *Collector {
	return &Collector{
		client: client,
		brti:   brti,
		feeds:  feeds,
		writer: writer,
		series: series,
	}
}

func (c *Collector) Run(ctx context.Context) error {
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

func (c *Collector) tick(ctx context.Context) {
	now := time.Now()
	brti := c.brti.Snapshot()
	c.brti.RecordSample()

	// Snapshot individual feeds
	var coinbase, kraken, bitstamp, binance float64
	for _, f := range c.feeds {
		switch f.Name() {
		case "coinbase":
			coinbase = f.MidPrice()
		case "kraken":
			kraken = f.MidPrice()
		case "bitstamp":
			bitstamp = f.MidPrice()
		case "binance":
			binance = f.MidPrice()
		}
	}

	// Fetch active Kalshi markets (both open and closed to capture settlements)
	var snaps []MarketSnap

	// Fetch both open and closed markets to capture settlements
	openMarkets, err := c.client.GetMarkets(ctx, c.series, "open")
	if err != nil {
		slog.Debug("tick: open market fetch failed", "err", err)
	}

	closedMarkets, err := c.client.GetMarkets(ctx, c.series, "closed")
	if err != nil {
		slog.Debug("tick: closed market fetch failed", "err", err)
	}

	// Combine both lists
	var allMarkets []kalshi.Market
	allMarkets = append(allMarkets, openMarkets...)
	allMarkets = append(allMarkets, closedMarkets...)

	// Build snapshots for all markets
	for _, m := range allMarkets {
		expiry, _ := m.ExpirationParsed()
		secsLeft := int(time.Until(expiry).Seconds())
		if secsLeft < 0 {
			secsLeft = 0
		}

		snaps = append(snaps, MarketSnap{
			Ticker:    m.Ticker,
			YesBid:    m.YesBid,
			YesAsk:    m.YesAsk,
			LastPrice: m.LastPrice,
			Volume:    m.Volume,
			OpenInt:   m.OpenInterest,
			Strike:    m.StrikePrice(),
			SecsLeft:  secsLeft,
			Status:    m.Status,
			Result:    m.Result,
		})
	}

	rec := TickRecord{
		Type:     "tick",
		Ts:       now.UTC().Format(time.RFC3339Nano),
		BRTI:     brti,
		Coinbase: coinbase,
		Kraken:   kraken,
		Bitstamp: bitstamp,
		Binance:  binance,
		Markets:  snaps,
	}

	if err := c.writer.Write(rec); err != nil {
		slog.Warn("tick: write failed", "err", err)
	}
}
