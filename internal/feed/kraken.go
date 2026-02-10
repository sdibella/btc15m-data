package feed

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/gorilla/websocket"
)

// KrakenFeed streams BTC-USD spread from Kraken WebSocket v2.
type KrakenFeed struct {
	baseFeed
}

func NewKrakenFeed() *KrakenFeed {
	return &KrakenFeed{baseFeed: baseFeed{name: "kraken"}}
}

type krakenSubscribe struct {
	Method string         `json:"method"`
	Params krakenSubParams `json:"params"`
}

type krakenSubParams struct {
	Channel string   `json:"channel"`
	Symbol  []string `json:"symbol"`
}

func (f *KrakenFeed) Run(ctx context.Context) error {
	const wsURL = "wss://ws.kraken.com/v2"

	for {
		if err := f.connect(ctx, wsURL); err != nil {
			slog.Warn("kraken ws disconnected", "err", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			slog.Info("kraken reconnecting...")
		}
	}
}

func (f *KrakenFeed) connect(ctx context.Context, wsURL string) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return err
	}
	defer conn.Close()

	sub := krakenSubscribe{
		Method: "subscribe",
		Params: krakenSubParams{
			Channel: "ticker",
			Symbol:  []string{"BTC/USD"},
		},
	}
	if err := conn.WriteJSON(sub); err != nil {
		return err
	}
	slog.Info("kraken subscribed")

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

		// Kraken v2 sends: {"channel":"ticker","type":"update","data":[{"symbol":"BTC/USD","bid":...,"ask":...}]}
		var envelope struct {
			Channel string            `json:"channel"`
			Type    string            `json:"type"`
			Data    []json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(msg, &envelope); err != nil {
			continue
		}
		if envelope.Channel != "ticker" || len(envelope.Data) == 0 {
			continue
		}

		var ticker struct {
			Bid float64 `json:"bid"`
			Ask float64 `json:"ask"`
		}
		if err := json.Unmarshal(envelope.Data[0], &ticker); err != nil {
			continue
		}

		bid := ticker.Bid
		ask := ticker.Ask
		if bid <= 0 || ask <= 0 {
			continue
		}

		mid := (bid + ask) / 2
		f.setPrice(mid)
	}
}
