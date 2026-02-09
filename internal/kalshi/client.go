package kalshi

import (
	"context"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"time"

	"github.com/gw/btc15m-data/internal/config"
)

type Client struct {
	cfg            *config.Config
	privKey        *rsa.PrivateKey
	http           *http.Client
	baseURL        string
	basePathPrefix string
}

func NewClient(cfg *config.Config) (*Client, error) {
	key, err := LoadPrivateKey(cfg.KalshiPrivKeyPath)
	if err != nil {
		return nil, fmt.Errorf("loading kalshi key: %w", err)
	}

	parsed, err := url.Parse(cfg.BaseURL())
	if err != nil {
		return nil, fmt.Errorf("parsing base URL: %w", err)
	}

	return &Client{
		cfg:            cfg,
		privKey:        key,
		http:           &http.Client{Timeout: 10 * time.Second},
		baseURL:        cfg.BaseURL(),
		basePathPrefix: parsed.Path,
	}, nil
}

func (c *Client) signPath(path string) string {
	return c.basePathPrefix + path
}

// --- API Types ---

type Market struct {
	Ticker                 string  `json:"ticker"`
	EventTicker            string  `json:"event_ticker"`
	Title                  string  `json:"title"`
	Status                 string  `json:"status"`
	YesBid                 int     `json:"yes_bid"`
	YesAsk                 int     `json:"yes_ask"`
	NoBid                  int     `json:"no_bid"`
	NoAsk                  int     `json:"no_ask"`
	LastPrice              int     `json:"last_price"`
	Volume                 int     `json:"volume"`
	OpenInterest           int     `json:"open_interest"`
	FloorStrike            float64 `json:"floor_strike"`
	CapStrike              float64 `json:"cap_strike"`
	CloseTime              string  `json:"close_time"`
	OpenTime               string  `json:"open_time"`
	ExpirationTime         string  `json:"expiration_time"`
	ExpectedExpirationTime string  `json:"expected_expiration_time"`
	Result                 string  `json:"result"`
	Subtitle               string  `json:"subtitle"`
	YesSubTitle            string  `json:"yes_sub_title"`
	NoSubTitle             string  `json:"no_sub_title"`
	CustomStrike           string  `json:"custom_strike"`
	RulesPrimary           string  `json:"rules_primary"`
}

func (m *Market) StrikePrice() float64 {
	if m.CapStrike > 0 {
		return m.CapStrike
	}
	if m.FloorStrike > 0 {
		return m.FloorStrike
	}

	if m.RulesPrimary != "" {
		var strike float64
		if _, err := fmt.Sscanf(m.RulesPrimary, "%*s at least %f", &strike); err == nil && strike > 0 {
			return strike
		}
		re := regexp.MustCompile(`is at least ([\d.]+)`)
		if matches := re.FindStringSubmatch(m.RulesPrimary); len(matches) > 1 {
			if strike, err := strconv.ParseFloat(matches[1], 64); err == nil {
				return strike
			}
		}
	}

	return 0
}

func (m *Market) ExpirationParsed() (time.Time, error) {
	if m.ExpectedExpirationTime != "" {
		return time.Parse(time.RFC3339, m.ExpectedExpirationTime)
	}
	return time.Parse(time.RFC3339, m.ExpirationTime)
}

type Balance struct {
	Balance int `json:"balance"`
}

// --- API Methods ---

func (c *Client) GetMarkets(ctx context.Context, seriesTicker string, status string) ([]Market, error) {
	params := url.Values{}
	if seriesTicker != "" {
		params.Set("series_ticker", seriesTicker)
	}
	if status != "" {
		params.Set("status", status)
	}
	params.Set("limit", "200")

	var result struct {
		Markets []Market `json:"markets"`
		Cursor  string   `json:"cursor"`
	}
	if err := c.get(ctx, "/markets", params, &result); err != nil {
		return nil, err
	}
	return result.Markets, nil
}

func (c *Client) GetBalance(ctx context.Context) (*Balance, error) {
	var result Balance
	if err := c.get(ctx, "/portfolio/balance", nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// --- HTTP helpers ---

func (c *Client) get(ctx context.Context, path string, params url.Values, out interface{}) error {
	reqURL := c.baseURL + path
	if params != nil && len(params) > 0 {
		reqURL += "?" + params.Encode()
	}

	req, err := http.NewRequestWithContext(ctx, "GET", reqURL, nil)
	if err != nil {
		return err
	}

	headers, err := AuthHeaders(c.cfg, c.privKey, "GET", c.signPath(path))
	if err != nil {
		return err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	req.Header.Set("Accept", "application/json")

	return c.doRequest(req, out)
}

func (c *Client) doRequest(req *http.Request, out interface{}) error {
	slog.Debug("kalshi request", "method", req.Method, "url", req.URL.String())

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("kalshi request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("reading response: %w", err)
	}

	if resp.StatusCode >= 400 {
		slog.Error("kalshi API error", "status", resp.StatusCode, "body", string(body))
		return fmt.Errorf("kalshi API error %d: %s", resp.StatusCode, string(body))
	}

	if out != nil {
		if err := json.Unmarshal(body, out); err != nil {
			return fmt.Errorf("decoding response: %w (body: %s)", err, string(body))
		}
	}

	return nil
}
