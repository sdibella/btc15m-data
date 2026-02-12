package tradelog

import "time"

type Order struct {
	OrderID           string
	Ticker            string
	Action            string // "buy" or "sell"
	Side              string // "yes" or "no"
	Type              string // "limit" or "market"
	YesPrice          int
	NoPrice           int
	Quantity          int
	FilledQuantity    int
	RemainingQuantity int
	AvgFillPrice      int
	Status            string // "resting", "canceled", "executed", "pending"
	CreatedTime       time.Time
	UpdatedTime       time.Time
}

type Fill struct {
	TradeID     string
	OrderID     string
	Ticker      string
	Side        string
	Action      string
	YesPrice    int
	NoPrice     int
	Count       int
	IsTaker     bool
	CreatedTime time.Time
}

type Settlement struct {
	Ticker       string
	MarketResult string
	NoTotalCount int
	NoCost       int
	YesTotalCount int
	YesCost      int
	Revenue      int
	SettledTime  time.Time
}

// DailyPnL is a row from the v_daily_pnl view.
type DailyPnL struct {
	Date    string
	Revenue int
	Cost    int
	NetPnL  int
	Trades  int
}

// Position is a row from the v_positions view.
type Position struct {
	Ticker       string
	YesContracts int
	NoContracts  int
	YesCost      int
	NoCost       int
	MarketResult string
	Revenue      int
}
