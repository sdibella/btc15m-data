package collector

import (
	"context"
	"log/slog"
	"strings"
	"sync"
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
	Markets  []MarketSnap `json:"markets,omitempty"`
}

// MarketSnap is a point-in-time snapshot of a Kalshi market.
type MarketSnap struct {
	Ticker    string   `json:"ticker"`
	YesBid    int      `json:"yes_bid"`
	YesAsk    int      `json:"yes_ask"`
	LastPrice int      `json:"last_price"`
	Volume    int      `json:"volume"`
	OpenInt   int      `json:"open_interest"`
	Strike    float64  `json:"strike,omitempty"`
	SecsLeft  int      `json:"secs_left"`
	Status    string   `json:"status,omitempty"`
	Result    string   `json:"result,omitempty"`
	YesBook   [][2]int `json:"yes_book,omitempty"`
	NoBook    [][2]int `json:"no_book,omitempty"`
}

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

func New(client *kalshi.Client, kalshiWS *kalshi.KalshiFeed, brti *feed.BRTIProxy, feeds []feed.ExchangeFeed, writer *Writer, series string) *Collector {
	return &Collector{
		client:   client,
		kalshiWS: kalshiWS,
		brti:     brti,
		feeds:    feeds,
		writer:   writer,
		series:   series,
	}
}

func (c *Collector) Run(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	// Start watchdog
	go c.watchdog(ctx, cancel)

	// Start market discovery loop (REST for metadata + subscription management)
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

// discoveryLoop fetches market metadata via REST and manages WS subscriptions.
// Runs every 30s normally, every 5s near market rotation boundaries (:00/:15/:30/:45).
func (c *Collector) discoveryLoop(ctx context.Context) {
	c.discover(ctx)

	for {
		interval := c.discoveryInterval()
		select {
		case <-ctx.Done():
			return
		case <-time.After(interval):
			c.discover(ctx)
		}
	}
}

func (c *Collector) discoveryInterval() time.Duration {
	min := time.Now().Minute() % 15
	if min <= 1 || min >= 14 {
		return 5 * time.Second // Near market rotation
	}
	return 30 * time.Second
}

func (c *Collector) discover(ctx context.Context) {
	openMarkets, err := c.client.GetMarkets(ctx, c.series, "open")
	if err != nil {
		slog.Debug("discover: open market fetch failed", "err", err)
	}

	closedMarkets, err := c.client.GetMarkets(ctx, c.series, "closed")
	if err != nil {
		slog.Debug("discover: closed market fetch failed", "err", err)
	}

	var allMarkets []kalshi.Market
	allMarkets = append(allMarkets, openMarkets...)
	allMarkets = append(allMarkets, closedMarkets...)

	if c.kalshiWS != nil && len(allMarkets) > 0 {
		c.kalshiWS.UpdateMetadata(allMarkets)

		tickers := make([]string, len(allMarkets))
		for i, m := range allMarkets {
			tickers[i] = m.Ticker
		}
		c.kalshiWS.UpdateSubscriptions(tickers)
	}
}

func (c *Collector) tick(ctx context.Context) {
	defer func() {
		if r := recover(); r != nil {
			slog.Error("tick panic recovered", "panic", r)
		}
	}()

	now := time.Now()
	brti := c.brti.Snapshot()
	c.brti.RecordSample()

	// Snapshot individual feeds
	var coinbase, kraken, bitstamp float64
	for _, f := range c.feeds {
		switch f.Name() {
		case "coinbase":
			coinbase = f.MidPrice()
		case "kraken":
			kraken = f.MidPrice()
		case "bitstamp":
			bitstamp = f.MidPrice()
		}
	}

	// Get Kalshi market data: WS when connected, REST fallback otherwise
	var snaps []MarketSnap
	if c.kalshiWS != nil && c.kalshiWS.IsConnected() {
		for _, ms := range c.kalshiWS.Snapshot() {
			snaps = append(snaps, MarketSnap{
				Ticker:    ms.Ticker,
				YesBid:    ms.YesBid,
				YesAsk:    ms.YesAsk,
				LastPrice: ms.LastPrice,
				Volume:    ms.Volume,
				OpenInt:   ms.OpenInterest,
				Strike:    ms.Strike,
				SecsLeft:  ms.SecsLeft,
				Status:    ms.Status,
				Result:    ms.Result,
				YesBook:   ms.YesBook,
				NoBook:    ms.NoBook,
			})
		}
	} else {
		snaps = c.restFallback(ctx)
	}

	rec := TickRecord{
		Type:     "tick",
		Ts:       now.UTC().Format(time.RFC3339Nano),
		BRTI:     brti,
		Coinbase: coinbase,
		Kraken:   kraken,
		Bitstamp: bitstamp,
		Markets:  snaps,
	}

	if err := c.writer.Write(rec); err != nil {
		slog.Warn("tick: write failed", "err", err)
	} else {
		c.lastWriteMu.Lock()
		c.lastWriteTime = time.Now()
		c.tickCount++
		c.lastWriteMu.Unlock()
	}
}

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

// restFallback fetches market data directly via REST (current behavior, no orderbook depth).
func (c *Collector) restFallback(ctx context.Context) []MarketSnap {
	openMarkets, err := c.client.GetMarkets(ctx, c.series, "open")
	if err != nil {
		slog.Debug("tick: open market fetch failed", "err", err)
	}

	closedMarkets, err := c.client.GetMarkets(ctx, c.series, "closed")
	if err != nil {
		slog.Debug("tick: closed market fetch failed", "err", err)
	}

	var allMarkets []kalshi.Market
	allMarkets = append(allMarkets, openMarkets...)
	allMarkets = append(allMarkets, closedMarkets...)

	var snaps []MarketSnap
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
	return snaps
}
