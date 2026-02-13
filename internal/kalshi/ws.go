package kalshi

import (
	"context"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/gw/btc15m-data/internal/config"
)

// KalshiFeed is a WebSocket client for Kalshi real-time market data.
// It provides ticker prices, orderbook depth, and merges with REST metadata.
type KalshiFeed struct {
	cfg     *config.Config
	privKey *rsa.PrivateKey
	wsURL   string

	mu       sync.RWMutex
	prices   map[string]*MarketPrice  // ticker → WS ticker data
	books    map[string]*Orderbook    // ticker → full depth book
	metadata map[string]*MarketMeta   // ticker → REST metadata

	// desiredTickers is the set of markets we want subscribed (set by UpdateSubscriptions).
	desiredTickers map[string]bool

	// Write-side state: conn, subscription tracking, command sequence.
	// Protected by writeMu. Lock ordering: mu before writeMu.
	writeMu           sync.Mutex
	conn              *websocket.Conn
	tickerSID         int
	orderbookSID      int
	subscribedTickers map[string]bool
	cmdSeq            int64

	connected atomic.Bool
}

// MarketPrice holds real-time ticker data from WS.
type MarketPrice struct {
	YesBid       int
	YesAsk       int
	LastPrice    int
	Volume       int
	OpenInterest int
}

// MarketMeta holds REST-sourced metadata for a market.
type MarketMeta struct {
	Status string
	Result string
	Strike float64
	Expiry time.Time
}

// Orderbook holds the depth for one market's YES and NO sides.
type Orderbook struct {
	Yes   map[int]int // price_cents → quantity
	No    map[int]int
	Ready bool
}

// MarketSnapshot is the merged WS+REST view of a single market.
type MarketSnapshot struct {
	Ticker       string
	Status       string
	Result       string
	YesBid       int
	YesAsk       int
	LastPrice    int
	Volume       int
	OpenInterest int
	SecsLeft     int
	Strike       float64
	YesBook      [][2]int
	NoBook       [][2]int
	FromWS       bool
}

// NewKalshiFeed creates a new WebSocket feed client.
func NewKalshiFeed(cfg *config.Config, privKey *rsa.PrivateKey) *KalshiFeed {
	return &KalshiFeed{
		cfg:               cfg,
		privKey:           privKey,
		wsURL:             cfg.WSBaseURL(),
		prices:            make(map[string]*MarketPrice),
		books:             make(map[string]*Orderbook),
		metadata:          make(map[string]*MarketMeta),
		desiredTickers:    make(map[string]bool),
		subscribedTickers: make(map[string]bool),
	}
}

// IsConnected returns true if the WebSocket is currently connected.
func (f *KalshiFeed) IsConnected() bool {
	return f.connected.Load()
}

// Run maintains the WebSocket connection with automatic reconnection.
func (f *KalshiFeed) Run(ctx context.Context) error {
	for {
		if err := f.connect(ctx); err != nil {
			slog.Warn("kalshi ws disconnected", "err", err)
		}
		f.connected.Store(false)

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			slog.Info("kalshi ws reconnecting...")
		}
	}
}

func (f *KalshiFeed) connect(ctx context.Context) error {
	conn, err := f.dial(ctx)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}

	// Reset write-side state
	f.writeMu.Lock()
	f.conn = conn
	f.tickerSID = 0
	f.orderbookSID = 0
	f.subscribedTickers = make(map[string]bool)
	f.cmdSeq = 0
	f.writeMu.Unlock()

	// Clear orderbooks (fresh snapshots arrive after subscribe)
	f.mu.Lock()
	f.books = make(map[string]*Orderbook)
	f.mu.Unlock()

	// Subscribe to desired tickers before marking connected
	f.mu.RLock()
	tickers := make([]string, 0, len(f.desiredTickers))
	for t := range f.desiredTickers {
		tickers = append(tickers, t)
	}
	f.mu.RUnlock()

	if len(tickers) > 0 {
		f.writeMu.Lock()
		err := f.subscribeLocked(tickers)
		f.writeMu.Unlock()
		if err != nil {
			conn.Close()
			return fmt.Errorf("subscribe: %w", err)
		}
	}

	f.connected.Store(true)
	slog.Info("kalshi ws connected", "subscriptions", len(tickers))

	// Run read loop with ping keepalive
	ctx2, cancel := context.WithCancel(ctx)
	defer cancel()
	go f.pingLoop(ctx2, conn)
	return f.readLoop(ctx2, conn)
}

func (f *KalshiFeed) dial(ctx context.Context) (*websocket.Conn, error) {
	headers, err := AuthHeaders(f.cfg, f.privKey, "GET", "/trade-api/ws/v2")
	if err != nil {
		return nil, fmt.Errorf("auth headers: %w", err)
	}

	h := http.Header{}
	for k, v := range headers {
		h.Set(k, v)
	}

	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	conn, _, err := dialer.DialContext(ctx, f.wsURL, h)
	if err != nil {
		return nil, err
	}

	// Kalshi sends pings every ~10s. Reset read deadline on ping/pong.
	conn.SetPingHandler(func(data string) error {
		conn.SetReadDeadline(time.Now().Add(30 * time.Second))
		return conn.WriteControl(websocket.PongMessage, []byte(data), time.Now().Add(5*time.Second))
	})
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(30 * time.Second))
		return nil
	})
	conn.SetReadDeadline(time.Now().Add(30 * time.Second))

	return conn, nil
}

func (f *KalshiFeed) pingLoop(ctx context.Context, conn *websocket.Conn) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			// WriteControl is safe to call concurrently with other writes.
			if err := conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)); err != nil {
				slog.Debug("kalshi ws ping failed", "err", err)
				return
			}
		}
	}
}

// --- WS message types ---

type wsCommand struct {
	ID     int64       `json:"id"`
	Cmd    string      `json:"cmd"`
	Params interface{} `json:"params"`
}

type subscribeParams struct {
	Channels      []string `json:"channels"`
	MarketTickers []string `json:"market_tickers"`
}

type updateSubParams struct {
	SIDs          []int    `json:"sids"`
	MarketTickers []string `json:"market_tickers"`
	Action        string   `json:"action"`
}

type wsEnvelope struct {
	ID   int64           `json:"id,omitempty"`
	Type string          `json:"type"`
	SID  int             `json:"sid,omitempty"`
	Seq  int             `json:"seq,omitempty"`
	Msg  json.RawMessage `json:"msg"`
}

type subOKEntry struct {
	Channel string `json:"channel"`
	SID     int    `json:"sid"`
}

type tickerPayload struct {
	MarketTicker string `json:"market_ticker"`
	Price        int    `json:"price"`
	YesBid       int    `json:"yes_bid"`
	YesAsk       int    `json:"yes_ask"`
	Volume       int    `json:"volume"`
	OpenInterest int    `json:"open_interest"`
}

type obSnapshotPayload struct {
	MarketTicker string   `json:"market_ticker"`
	Yes          [][2]int `json:"yes"`
	No           [][2]int `json:"no"`
}

type obDeltaPayload struct {
	MarketTicker string `json:"market_ticker"`
	Price        int    `json:"price"`
	Delta        int    `json:"delta"`
	Side         string `json:"side"`
}

// --- Read loop ---

func (f *KalshiFeed) readLoop(ctx context.Context, conn *websocket.Conn) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		_, msg, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		conn.SetReadDeadline(time.Now().Add(30 * time.Second))

		var env wsEnvelope
		if err := json.Unmarshal(msg, &env); err != nil {
			slog.Debug("kalshi ws: unmarshal error", "err", err)
			continue
		}

		switch env.Type {
		case "ticker":
			f.handleTicker(env.Msg)
		case "orderbook_snapshot":
			f.handleOrderbookSnapshot(env.Msg)
		case "orderbook_delta":
			f.handleOrderbookDelta(env.Msg)
		case "ok":
			f.handleOK(env.Msg)
		case "error":
			slog.Warn("kalshi ws error", "id", env.ID, "msg", string(env.Msg))
		default:
			slog.Debug("kalshi ws: unknown message type", "type", env.Type)
		}
	}
}

func (f *KalshiFeed) handleTicker(raw json.RawMessage) {
	var t tickerPayload
	if err := json.Unmarshal(raw, &t); err != nil {
		slog.Debug("kalshi ws: ticker unmarshal error", "err", err)
		return
	}

	f.mu.Lock()
	p, ok := f.prices[t.MarketTicker]
	if !ok {
		p = &MarketPrice{}
		f.prices[t.MarketTicker] = p
	}
	p.YesBid = t.YesBid
	p.YesAsk = t.YesAsk
	p.LastPrice = t.Price
	p.Volume = t.Volume
	p.OpenInterest = t.OpenInterest
	f.mu.Unlock()

	slog.Debug("ws ticker", "ticker", t.MarketTicker, "bid", t.YesBid, "ask", t.YesAsk)
}

func (f *KalshiFeed) handleOrderbookSnapshot(raw json.RawMessage) {
	var snap obSnapshotPayload
	if err := json.Unmarshal(raw, &snap); err != nil {
		slog.Debug("kalshi ws: ob snapshot unmarshal error", "err", err)
		return
	}

	yes := make(map[int]int, len(snap.Yes))
	for _, level := range snap.Yes {
		yes[level[0]] = level[1]
	}
	no := make(map[int]int, len(snap.No))
	for _, level := range snap.No {
		no[level[0]] = level[1]
	}

	f.mu.Lock()
	f.books[snap.MarketTicker] = &Orderbook{Yes: yes, No: no, Ready: true}
	f.mu.Unlock()

	slog.Debug("ws ob snapshot", "ticker", snap.MarketTicker,
		"yes_levels", len(yes), "no_levels", len(no))
}

func (f *KalshiFeed) handleOrderbookDelta(raw json.RawMessage) {
	var d obDeltaPayload
	if err := json.Unmarshal(raw, &d); err != nil {
		slog.Debug("kalshi ws: ob delta unmarshal error", "err", err)
		return
	}

	f.mu.Lock()
	book, ok := f.books[d.MarketTicker]
	if !ok || !book.Ready {
		f.mu.Unlock()
		return
	}

	var side map[int]int
	if d.Side == "yes" {
		side = book.Yes
	} else {
		side = book.No
	}

	side[d.Price] += d.Delta
	if side[d.Price] <= 0 {
		delete(side, d.Price)
	}
	f.mu.Unlock()
}

func (f *KalshiFeed) handleOK(raw json.RawMessage) {
	// Parse subscribe OK responses to capture SIDs.
	// update_subscription OK responses may have different formats; ignore errors.
	var entries []subOKEntry
	if err := json.Unmarshal(raw, &entries); err != nil {
		return
	}

	f.writeMu.Lock()
	for _, e := range entries {
		switch e.Channel {
		case "ticker":
			f.tickerSID = e.SID
		case "orderbook_delta":
			f.orderbookSID = e.SID
		}
		slog.Debug("ws subscribed", "channel", e.Channel, "sid", e.SID)
	}
	f.writeMu.Unlock()
}

// --- Subscription management ---

// subscribeLocked sends a subscribe command. Caller must hold writeMu.
func (f *KalshiFeed) subscribeLocked(tickers []string) error {
	f.cmdSeq++
	cmd := wsCommand{
		ID:  f.cmdSeq,
		Cmd: "subscribe",
		Params: subscribeParams{
			Channels:      []string{"ticker", "orderbook_delta"},
			MarketTickers: tickers,
		},
	}
	f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
	if err := f.conn.WriteJSON(cmd); err != nil {
		return err
	}
	f.conn.SetWriteDeadline(time.Time{})
	for _, t := range tickers {
		f.subscribedTickers[t] = true
	}
	slog.Debug("ws subscribe sent", "count", len(tickers))
	return nil
}

// UpdateSubscriptions adjusts which markets the WS is subscribed to.
// Called by the collector's discovery loop.
func (f *KalshiFeed) UpdateSubscriptions(tickers []string) {
	desired := make(map[string]bool, len(tickers))
	for _, t := range tickers {
		desired[t] = true
	}

	f.mu.Lock()
	f.desiredTickers = desired
	f.mu.Unlock()

	if !f.connected.Load() {
		return
	}

	f.writeMu.Lock()

	if f.conn == nil {
		f.writeMu.Unlock()
		return
	}

	// Compute diff against currently subscribed tickers
	var toAdd, toRemove []string
	for t := range desired {
		if !f.subscribedTickers[t] {
			toAdd = append(toAdd, t)
		}
	}
	for t := range f.subscribedTickers {
		if !desired[t] {
			toRemove = append(toRemove, t)
		}
	}

	// Add new markets
	if len(toAdd) > 0 {
		if f.tickerSID == 0 {
			// No existing subscription yet — send fresh subscribe
			f.cmdSeq++
			cmd := wsCommand{
				ID:  f.cmdSeq,
				Cmd: "subscribe",
				Params: subscribeParams{
					Channels:      []string{"ticker", "orderbook_delta"},
					MarketTickers: toAdd,
				},
			}
			f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := f.conn.WriteJSON(cmd); err != nil {
				slog.Warn("ws subscribe failed", "err", err)
			}
		} else {
			// Update existing subscription
			f.cmdSeq++
			cmd := wsCommand{
				ID:  f.cmdSeq,
				Cmd: "update_subscription",
				Params: updateSubParams{
					SIDs:          []int{f.tickerSID, f.orderbookSID},
					MarketTickers: toAdd,
					Action:        "add_markets",
				},
			}
			f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := f.conn.WriteJSON(cmd); err != nil {
				slog.Warn("ws update_subscription add failed", "err", err)
			}
		}
		for _, t := range toAdd {
			f.subscribedTickers[t] = true
		}
		slog.Debug("ws added markets", "count", len(toAdd))
	}

	// Remove expired markets
	if len(toRemove) > 0 && f.tickerSID != 0 {
		f.cmdSeq++
		cmd := wsCommand{
			ID:  f.cmdSeq,
			Cmd: "update_subscription",
			Params: updateSubParams{
				SIDs:          []int{f.tickerSID, f.orderbookSID},
				MarketTickers: toRemove,
				Action:        "remove_markets",
			},
		}
		f.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
		if err := f.conn.WriteJSON(cmd); err != nil {
			slog.Warn("ws update_subscription remove failed", "err", err)
		}
		for _, t := range toRemove {
			delete(f.subscribedTickers, t)
		}
		slog.Debug("ws removed markets", "count", len(toRemove))
	}

	f.conn.SetWriteDeadline(time.Time{})
	f.writeMu.Unlock()

	// Clean up caches for removed tickers
	if len(toRemove) > 0 {
		f.mu.Lock()
		for _, t := range toRemove {
			delete(f.prices, t)
			delete(f.books, t)
			delete(f.metadata, t)
		}
		f.mu.Unlock()
	}
}

// UpdateMetadata pushes REST-sourced metadata into the feed cache.
func (f *KalshiFeed) UpdateMetadata(markets []Market) {
	f.mu.Lock()
	defer f.mu.Unlock()

	for i := range markets {
		m := &markets[i]
		expiry, _ := m.ExpirationParsed()
		f.metadata[m.Ticker] = &MarketMeta{
			Status: m.Status,
			Result: m.Result,
			Strike: m.StrikePrice(),
			Expiry: expiry,
		}
	}
}

// Snapshot returns a merged view of all tracked markets (WS prices + REST metadata).
func (f *KalshiFeed) Snapshot() []MarketSnapshot {
	f.mu.RLock()
	defer f.mu.RUnlock()

	result := make([]MarketSnapshot, 0, len(f.metadata))
	for ticker, meta := range f.metadata {
		snap := MarketSnapshot{
			Ticker: ticker,
			Status: meta.Status,
			Result: meta.Result,
			Strike: meta.Strike,
			FromWS: true,
		}

		secsLeft := int(time.Until(meta.Expiry).Seconds())
		if secsLeft < 0 {
			secsLeft = 0
		}
		snap.SecsLeft = secsLeft

		// Merge WS price data
		if price, ok := f.prices[ticker]; ok {
			snap.YesBid = price.YesBid
			snap.YesAsk = price.YesAsk
			snap.LastPrice = price.LastPrice
			snap.Volume = price.Volume
			snap.OpenInterest = price.OpenInterest
		}

		// Merge orderbook data
		if book, ok := f.books[ticker]; ok && book.Ready {
			snap.YesBook = sortedLevels(book.Yes)
			snap.NoBook = sortedLevels(book.No)
		}

		result = append(result, snap)
	}
	return result
}

// sortedLevels converts a price→qty map to a sorted [][2]int slice.
func sortedLevels(m map[int]int) [][2]int {
	if len(m) == 0 {
		return nil
	}
	levels := make([][2]int, 0, len(m))
	for price, qty := range m {
		levels = append(levels, [2]int{price, qty})
	}
	sort.Slice(levels, func(i, j int) bool {
		return levels[i][0] < levels[j][0]
	})
	return levels
}
