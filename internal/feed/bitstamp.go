package feed

import (
	"context"
	"encoding/json"
	"log/slog"
	"strconv"
	"time"

	"github.com/gorilla/websocket"
)

// BitstampFeed streams BTC-USD order book from Bitstamp WebSocket.
type BitstampFeed struct {
	baseFeed
}

func NewBitstampFeed() *BitstampFeed {
	return &BitstampFeed{baseFeed: baseFeed{name: "bitstamp"}}
}

type bitstampSubscribe struct {
	Event string          `json:"event"`
	Data  bitstampSubData `json:"data"`
}

type bitstampSubData struct {
	Channel string `json:"channel"`
}

func (f *BitstampFeed) Run(ctx context.Context) error {
	const wsURL = "wss://ws.bitstamp.net"

	for {
		if err := f.connect(ctx, wsURL); err != nil {
			slog.Warn("bitstamp ws disconnected", "err", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			slog.Info("bitstamp reconnecting...")
		}
	}
}

func (f *BitstampFeed) connect(ctx context.Context, wsURL string) error {
	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	conn, _, err := dialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return err
	}
	defer conn.Close()

	sub := bitstampSubscribe{
		Event: "bts:subscribe",
		Data:  bitstampSubData{Channel: "order_book_btcusd"},
	}
	if err := conn.WriteJSON(sub); err != nil {
		return err
	}
	slog.Info("bitstamp subscribed")

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		conn.SetReadDeadline(time.Now().Add(30 * time.Second))
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return err
		}

		var envelope struct {
			Event   string          `json:"event"`
			Channel string          `json:"channel"`
			Data    json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(msg, &envelope); err != nil {
			continue
		}

		// Skip non-data messages (subscription confirmations, etc.)
		if envelope.Event == "bts:subscription_succeeded" || envelope.Event == "bts:request_reconnect" {
			slog.Debug("bitstamp event", "event", envelope.Event)
			continue
		}

		var book struct {
			Bids [][]string `json:"bids"` // [[price, amount], ...]
			Asks [][]string `json:"asks"`
		}
		if err := json.Unmarshal(envelope.Data, &book); err != nil {
			continue
		}

		if len(book.Bids) == 0 || len(book.Asks) == 0 {
			continue
		}

		bid, err1 := strconv.ParseFloat(book.Bids[0][0], 64)
		ask, err2 := strconv.ParseFloat(book.Asks[0][0], 64)
		if err1 != nil || err2 != nil {
			continue
		}

		mid := (bid + ask) / 2
		f.setPrice(mid)
	}
}
