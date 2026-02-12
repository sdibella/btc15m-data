package tradelog

import (
	"context"
	"log/slog"
	"time"

	"github.com/gw/btc15m-data/internal/kalshi"
)

// Sync fetches all orders, fills, and settlements from Kalshi and stores them.
func Sync(ctx context.Context, client *kalshi.Client, store *Store) error {
	if err := syncOrders(ctx, client, store); err != nil {
		return err
	}
	if err := syncFills(ctx, client, store); err != nil {
		return err
	}
	return syncSettlements(ctx, client, store)
}

func syncOrders(ctx context.Context, client *kalshi.Client, store *Store) error {
	var cursor string
	total := 0
	for {
		orders, next, err := client.GetOrders(ctx, kalshi.OrderParams{Cursor: cursor})
		if err != nil {
			return err
		}
		for _, o := range orders {
			local := kalshiOrderToLocal(o)
			if err := store.UpsertOrder(ctx, &local); err != nil {
				return err
			}
			total++
		}
		if next == "" || len(orders) == 0 {
			break
		}
		cursor = next
	}
	slog.Info("synced orders", "count", total)
	return nil
}

func syncFills(ctx context.Context, client *kalshi.Client, store *Store) error {
	var cursor string
	total := 0
	for {
		fills, next, err := client.GetFills(ctx, kalshi.FillParams{Cursor: cursor})
		if err != nil {
			return err
		}
		for _, f := range fills {
			local := kalshiFillToLocal(f)
			if err := store.InsertFill(ctx, &local); err != nil {
				return err
			}
			total++
		}
		if next == "" || len(fills) == 0 {
			break
		}
		cursor = next
	}
	slog.Info("synced fills", "count", total)
	return nil
}

func syncSettlements(ctx context.Context, client *kalshi.Client, store *Store) error {
	var cursor string
	total := 0
	for {
		settlements, next, err := client.GetSettlements(ctx, kalshi.SettlementParams{Cursor: cursor})
		if err != nil {
			return err
		}
		for _, s := range settlements {
			local := kalshiSettlementToLocal(s)
			if err := store.UpsertSettlement(ctx, &local); err != nil {
				return err
			}
			total++
		}
		if next == "" || len(settlements) == 0 {
			break
		}
		cursor = next
	}
	slog.Info("synced settlements", "count", total)
	return nil
}

func parseTime(s string) time.Time {
	t, _ := time.Parse(time.RFC3339, s)
	return t
}

func kalshiOrderToLocal(o kalshi.Order) Order {
	return Order{
		OrderID:           o.OrderID,
		Ticker:            o.Ticker,
		Action:            o.Action,
		Side:              o.Side,
		Type:              o.Type,
		YesPrice:          o.YesPrice,
		NoPrice:           o.NoPrice,
		Quantity:          o.Quantity,
		FilledQuantity:    o.FilledQuantity,
		RemainingQuantity: o.RemainingQuantity,
		AvgFillPrice:      o.AvgFillPrice,
		Status:            o.Status,
		CreatedTime:       parseTime(o.CreatedTime),
		UpdatedTime:       parseTime(o.UpdatedTime),
	}
}

func kalshiFillToLocal(f kalshi.Fill) Fill {
	return Fill{
		TradeID:     f.TradeID,
		OrderID:     f.OrderID,
		Ticker:      f.Ticker,
		Side:        f.Side,
		Action:      f.Action,
		YesPrice:    f.YesPrice,
		NoPrice:     f.NoPrice,
		Count:       f.Count,
		IsTaker:     f.IsTaker,
		CreatedTime: parseTime(f.CreatedTime),
	}
}

func kalshiSettlementToLocal(s kalshi.Settlement) Settlement {
	return Settlement{
		Ticker:        s.Ticker,
		MarketResult:  s.MarketResult,
		NoTotalCount:  s.NoTotalCount,
		NoCost:        s.NoCost,
		YesTotalCount: s.YesTotalCount,
		YesCost:       s.YesCost,
		Revenue:       s.Revenue,
		SettledTime:   parseTime(s.SettledTime),
	}
}
