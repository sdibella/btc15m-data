package tradelog

const schemaDDL = `
CREATE TABLE IF NOT EXISTS orders (
	order_id          TEXT PRIMARY KEY,
	ticker            TEXT NOT NULL,
	action            TEXT NOT NULL,
	side              TEXT NOT NULL,
	type              TEXT NOT NULL,
	yes_price         INTEGER NOT NULL DEFAULT 0,
	no_price          INTEGER NOT NULL DEFAULT 0,
	quantity          INTEGER NOT NULL DEFAULT 0,
	filled_quantity   INTEGER NOT NULL DEFAULT 0,
	remaining_quantity INTEGER NOT NULL DEFAULT 0,
	avg_fill_price    INTEGER NOT NULL DEFAULT 0,
	status            TEXT NOT NULL DEFAULT '',
	created_time      DATETIME NOT NULL,
	updated_time      DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_time);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS fills (
	trade_id     TEXT PRIMARY KEY,
	order_id     TEXT NOT NULL REFERENCES orders(order_id),
	ticker       TEXT NOT NULL,
	side         TEXT NOT NULL,
	action       TEXT NOT NULL,
	yes_price    INTEGER NOT NULL DEFAULT 0,
	no_price     INTEGER NOT NULL DEFAULT 0,
	count        INTEGER NOT NULL DEFAULT 0,
	is_taker     BOOLEAN NOT NULL DEFAULT 0,
	created_time DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fills_ticker ON fills(ticker);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_created ON fills(created_time);

CREATE TABLE IF NOT EXISTS settlements (
	ticker         TEXT PRIMARY KEY,
	market_result  TEXT NOT NULL DEFAULT '',
	no_total_count INTEGER NOT NULL DEFAULT 0,
	no_cost        INTEGER NOT NULL DEFAULT 0,
	yes_total_count INTEGER NOT NULL DEFAULT 0,
	yes_cost       INTEGER NOT NULL DEFAULT 0,
	revenue        INTEGER NOT NULL DEFAULT 0,
	settled_time   DATETIME NOT NULL
);

CREATE VIEW IF NOT EXISTS v_positions AS
SELECT
	f.ticker,
	SUM(CASE WHEN f.side = 'yes' AND f.action = 'buy' THEN f.count
	         WHEN f.side = 'yes' AND f.action = 'sell' THEN -f.count
	         ELSE 0 END) AS yes_contracts,
	SUM(CASE WHEN f.side = 'no' AND f.action = 'buy' THEN f.count
	         WHEN f.side = 'no' AND f.action = 'sell' THEN -f.count
	         ELSE 0 END) AS no_contracts,
	SUM(CASE WHEN f.side = 'yes' AND f.action = 'buy' THEN f.yes_price * f.count
	         WHEN f.side = 'yes' AND f.action = 'sell' THEN -f.yes_price * f.count
	         ELSE 0 END) AS yes_cost,
	SUM(CASE WHEN f.side = 'no' AND f.action = 'buy' THEN f.no_price * f.count
	         WHEN f.side = 'no' AND f.action = 'sell' THEN -f.no_price * f.count
	         ELSE 0 END) AS no_cost,
	COALESCE(s.market_result, '') AS market_result,
	COALESCE(s.revenue, 0) AS revenue
FROM fills f
LEFT JOIN settlements s ON f.ticker = s.ticker
GROUP BY f.ticker;

CREATE VIEW IF NOT EXISTS v_daily_pnl AS
SELECT
	DATE(s.settled_time) AS date,
	SUM(s.revenue) AS revenue,
	SUM(s.yes_cost + s.no_cost) AS cost,
	SUM(s.revenue - s.yes_cost - s.no_cost) AS net_pnl,
	COUNT(*) AS trades
FROM settlements s
WHERE s.revenue != 0 OR s.yes_cost != 0 OR s.no_cost != 0
GROUP BY DATE(s.settled_time)
ORDER BY date;
`
