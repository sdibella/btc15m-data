package tradelog

import (
	"context"
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite"
)

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("opening db: %w", err)
	}

	// WAL mode for concurrent reads
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		db.Close()
		return nil, fmt.Errorf("setting WAL mode: %w", err)
	}

	// Run schema migration
	if _, err := db.Exec(schemaDDL); err != nil {
		db.Close()
		return nil, fmt.Errorf("schema migration: %w", err)
	}

	return &Store{db: db}, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) UpsertOrder(ctx context.Context, o *Order) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO orders (order_id, ticker, action, side, type, yes_price, no_price,
			quantity, filled_quantity, remaining_quantity, avg_fill_price, status,
			created_time, updated_time)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(order_id) DO UPDATE SET
			filled_quantity = excluded.filled_quantity,
			remaining_quantity = excluded.remaining_quantity,
			avg_fill_price = excluded.avg_fill_price,
			status = excluded.status,
			updated_time = excluded.updated_time`,
		o.OrderID, o.Ticker, o.Action, o.Side, o.Type,
		o.YesPrice, o.NoPrice, o.Quantity, o.FilledQuantity,
		o.RemainingQuantity, o.AvgFillPrice, o.Status,
		o.CreatedTime, o.UpdatedTime,
	)
	return err
}

func (s *Store) InsertFill(ctx context.Context, f *Fill) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT OR IGNORE INTO fills (trade_id, order_id, ticker, side, action,
			yes_price, no_price, count, is_taker, created_time)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		f.TradeID, f.OrderID, f.Ticker, f.Side, f.Action,
		f.YesPrice, f.NoPrice, f.Count, f.IsTaker, f.CreatedTime,
	)
	return err
}

func (s *Store) UpsertSettlement(ctx context.Context, st *Settlement) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO settlements (ticker, market_result, no_total_count, no_cost,
			yes_total_count, yes_cost, revenue, settled_time)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(ticker) DO UPDATE SET
			market_result = excluded.market_result,
			revenue = excluded.revenue,
			settled_time = excluded.settled_time`,
		st.Ticker, st.MarketResult, st.NoTotalCount, st.NoCost,
		st.YesTotalCount, st.YesCost, st.Revenue, st.SettledTime,
	)
	return err
}

func (s *Store) GetDailyPnL(ctx context.Context) ([]DailyPnL, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT date, revenue, cost, net_pnl, trades FROM v_daily_pnl`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []DailyPnL
	for rows.Next() {
		var d DailyPnL
		if err := rows.Scan(&d.Date, &d.Revenue, &d.Cost, &d.NetPnL, &d.Trades); err != nil {
			return nil, err
		}
		results = append(results, d)
	}
	return results, rows.Err()
}

func (s *Store) GetPositions(ctx context.Context) ([]Position, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT ticker, yes_contracts, no_contracts, yes_cost, no_cost, market_result, revenue
		FROM v_positions ORDER BY ticker`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []Position
	for rows.Next() {
		var p Position
		if err := rows.Scan(&p.Ticker, &p.YesContracts, &p.NoContracts,
			&p.YesCost, &p.NoCost, &p.MarketResult, &p.Revenue); err != nil {
			return nil, err
		}
		results = append(results, p)
	}
	return results, rows.Err()
}

func (s *Store) OpenPositions(ctx context.Context) ([]Position, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT ticker, yes_contracts, no_contracts, yes_cost, no_cost, market_result, revenue
		FROM v_positions
		WHERE market_result = ''
		ORDER BY ticker`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []Position
	for rows.Next() {
		var p Position
		if err := rows.Scan(&p.Ticker, &p.YesContracts, &p.NoContracts,
			&p.YesCost, &p.NoCost, &p.MarketResult, &p.Revenue); err != nil {
			return nil, err
		}
		results = append(results, p)
	}
	return results, rows.Err()
}

func (s *Store) RecentTrades(ctx context.Context, limit int) ([]Fill, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT trade_id, order_id, ticker, side, action, yes_price, no_price,
			count, is_taker, created_time
		FROM fills ORDER BY created_time DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []Fill
	for rows.Next() {
		var f Fill
		if err := rows.Scan(&f.TradeID, &f.OrderID, &f.Ticker, &f.Side, &f.Action,
			&f.YesPrice, &f.NoPrice, &f.Count, &f.IsTaker, &f.CreatedTime); err != nil {
			return nil, err
		}
		results = append(results, f)
	}
	return results, rows.Err()
}
