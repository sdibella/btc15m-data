package feed

import (
	"context"
	"encoding/json"
	"log/slog"
	"strconv"
	"time"

	"github.com/gorilla/websocket"
)

// CoinbaseFeed streams BTC-USD ticker from Coinbase WebSocket.
type CoinbaseFeed struct {
	baseFeed
}

func NewCoinbaseFeed() *CoinbaseFeed {
	return &CoinbaseFeed{baseFeed: baseFeed{name: "coinbase"}}
}

type coinbaseSubscribe struct {
	Type       string   `json:"type"`
	ProductIDs []string `json:"product_ids"`
	Channels   []string `json:"channels"`
}

type coinbaseTicker struct {
	Type      string `json:"type"`
	BestBid   string `json:"best_bid"`
	BestAsk   string `json:"best_ask"`
	ProductID string `json:"product_id"`
}

func (f *CoinbaseFeed) Run(ctx context.Context) error {
	const wsURL = "wss://ws-feed.exchange.coinbase.com"

	for {
		if err := f.connect(ctx, wsURL); err != nil {
			slog.Warn("coinbase ws disconnected", "err", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			slog.Info("coinbase reconnecting...")
		}
	}
}

func (f *CoinbaseFeed) connect(ctx context.Context, wsURL string) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return err
	}
	defer conn.Close()

	sub := coinbaseSubscribe{
		Type:       "subscribe",
		ProductIDs: []string{"BTC-USD"},
		Channels:   []string{"ticker"},
	}
	if err := conn.WriteJSON(sub); err != nil {
		return err
	}

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		conn.SetReadDeadline(time.Now().Add(10 * time.Second))
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return err
		}

		var ticker coinbaseTicker
		if err := json.Unmarshal(msg, &ticker); err != nil {
			continue
		}

		if ticker.Type != "ticker" {
			continue
		}

		bid, err1 := strconv.ParseFloat(ticker.BestBid, 64)
		ask, err2 := strconv.ParseFloat(ticker.BestAsk, 64)
		if err1 != nil || err2 != nil {
			continue
		}

		mid := (bid + ask) / 2
		f.setPrice(mid)
	}
}
