package feed

import (
	"context"
	"encoding/json"
	"log/slog"
	"strconv"
	"time"

	"github.com/gorilla/websocket"
)

// BinanceFeed streams BTC-USDT bookTicker from Binance WebSocket.
type BinanceFeed struct {
	baseFeed
}

func NewBinanceFeed() *BinanceFeed {
	return &BinanceFeed{baseFeed: baseFeed{name: "binance"}}
}

type binanceBookTicker struct {
	BestBidPrice string `json:"b"`
	BestAskPrice string `json:"a"`
}

func (f *BinanceFeed) Run(ctx context.Context) error {
	const wsURL = "wss://stream.binance.us:9443/ws/btcusdt@bookTicker"

	for {
		if err := f.connect(ctx, wsURL); err != nil {
			slog.Warn("binance ws disconnected", "err", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			slog.Info("binance reconnecting...")
		}
	}
}

func (f *BinanceFeed) connect(ctx context.Context, wsURL string) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return err
	}
	defer conn.Close()

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

		var ticker binanceBookTicker
		if err := json.Unmarshal(msg, &ticker); err != nil {
			continue
		}

		bid, err1 := strconv.ParseFloat(ticker.BestBidPrice, 64)
		ask, err2 := strconv.ParseFloat(ticker.BestAskPrice, 64)
		if err1 != nil || err2 != nil {
			continue
		}

		mid := (bid + ask) / 2
		f.setPrice(mid)
	}
}
