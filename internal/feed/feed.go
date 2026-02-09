package feed

import (
	"context"
	"log/slog"
	"math"
	"sort"
	"sync"
	"time"
)

type ExchangeFeed interface {
	Name() string
	Run(ctx context.Context) error
	MidPrice() float64
	LastUpdate() time.Time
	IsStale() bool // >5s since last update
}

type TimedPrice struct {
	Time  time.Time
	Price float64
}

type BRTIProxy struct {
	feeds           []ExchangeFeed
	mu              sync.RWMutex
	price           float64
	priceHistory    []TimedPrice // ring buffer, last 900 samples
	historyIdx      int
	historyFull     bool
	settlementTicks []float64 // 0-60 values during final minute
	sampling        bool
}

func NewBRTIProxy(feeds []ExchangeFeed) *BRTIProxy {
	return &BRTIProxy{
		feeds:        feeds,
		priceHistory: make([]TimedPrice, 900),
	}
}

// Snapshot computes the median of non-stale mid-prices.
func (b *BRTIProxy) Snapshot() float64 {
	var prices []float64
	for _, f := range b.feeds {
		if !f.IsStale() {
			p := f.MidPrice()
			if p > 0 {
				prices = append(prices, p)
			}
		}
	}

	if len(prices) == 0 {
		b.mu.RLock()
		defer b.mu.RUnlock()
		return b.price // return last known price
	}

	sort.Float64s(prices)
	median := median(prices)

	b.mu.Lock()
	b.price = median
	b.mu.Unlock()

	return median
}

// RecordSample appends the current snapshot to the price history ring buffer.
func (b *BRTIProxy) RecordSample() {
	p := b.Snapshot()
	if p <= 0 {
		return
	}

	b.mu.Lock()
	defer b.mu.Unlock()

	b.priceHistory[b.historyIdx] = TimedPrice{Time: time.Now(), Price: p}
	b.historyIdx++
	if b.historyIdx >= len(b.priceHistory) {
		b.historyIdx = 0
		b.historyFull = true
	}
}

// PriceHistory returns the most recent N prices from the ring buffer.
func (b *BRTIProxy) PriceHistory(n int) []float64 {
	b.mu.RLock()
	defer b.mu.RUnlock()

	total := b.historyIdx
	if b.historyFull {
		total = len(b.priceHistory)
	}
	if n > total {
		n = total
	}
	if n == 0 {
		return nil
	}

	result := make([]float64, n)
	for i := range n {
		idx := b.historyIdx - n + i
		if idx < 0 {
			idx += len(b.priceHistory)
		}
		result[i] = b.priceHistory[idx].Price
	}
	return result
}

// StartSettlementWindow begins recording per-second ticks for the final minute.
func (b *BRTIProxy) StartSettlementWindow() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.settlementTicks = make([]float64, 0, 60)
	b.sampling = true
	slog.Info("settlement window started")
}

// RecordSettlementTick records one per-second BRTI value during the final minute.
func (b *BRTIProxy) RecordSettlementTick() {
	p := b.Snapshot()
	if p <= 0 {
		return
	}

	b.mu.Lock()
	defer b.mu.Unlock()
	if b.sampling {
		b.settlementTicks = append(b.settlementTicks, p)
		slog.Debug("settlement tick", "k", len(b.settlementTicks), "price", p)
	}
}

// SettlementTicks returns the observed ticks so far.
func (b *BRTIProxy) SettlementTicks() []float64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]float64, len(b.settlementTicks))
	copy(out, b.settlementTicks)
	return out
}

// IsSampling returns whether we're in the final-minute settlement window.
func (b *BRTIProxy) IsSampling() bool {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.sampling
}

// StopSettlementWindow ends settlement sampling.
func (b *BRTIProxy) StopSettlementWindow() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.sampling = false
}

// SettlementAverage returns the average of all settlement ticks collected so far.
func (b *BRTIProxy) SettlementAverage() float64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if len(b.settlementTicks) == 0 {
		return 0
	}
	sum := 0.0
	for _, v := range b.settlementTicks {
		sum += v
	}
	return sum / float64(len(b.settlementTicks))
}

// Price returns the last computed proxy price.
func (b *BRTIProxy) Price() float64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.price
}

// FeedStatus returns a summary of each feed's health.
func (b *BRTIProxy) FeedStatus() []FeedHealth {
	var out []FeedHealth
	for _, f := range b.feeds {
		out = append(out, FeedHealth{
			Name:       f.Name(),
			Price:      f.MidPrice(),
			LastUpdate: f.LastUpdate(),
			Stale:      f.IsStale(),
		})
	}
	return out
}

type FeedHealth struct {
	Name       string
	Price      float64
	LastUpdate time.Time
	Stale      bool
}

func median(sorted []float64) float64 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	if n%2 == 0 {
		return (sorted[n/2-1] + sorted[n/2]) / 2
	}
	return sorted[n/2]
}

// baseFeed provides common atomic price storage for exchange feeds.
type baseFeed struct {
	name       string
	mu         sync.RWMutex
	midPrice   float64
	lastUpdate time.Time
}

func (b *baseFeed) Name() string { return b.name }

func (b *baseFeed) MidPrice() float64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.midPrice
}

func (b *baseFeed) LastUpdate() time.Time {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.lastUpdate
}

func (b *baseFeed) IsStale() bool {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.lastUpdate.IsZero() {
		return true
	}
	return time.Since(b.lastUpdate) > 5*time.Second
}

func (b *baseFeed) setPrice(price float64) {
	if math.IsNaN(price) || price <= 0 {
		return
	}
	b.mu.Lock()
	b.midPrice = price
	b.lastUpdate = time.Now()
	b.mu.Unlock()
}
