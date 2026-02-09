package config

import (
	"fmt"
	"os"

	"github.com/joho/godotenv"
)

type Config struct {
	KalshiAPIKeyID    string
	KalshiPrivKeyPath string
	KalshiEnv         string // "prod" or "demo"
	OutputDir         string // default "./data"
	SeriesTicker      string // default "KXBTC15M"
}

func (c *Config) BaseURL() string {
	if c.KalshiEnv == "prod" {
		return "https://api.elections.kalshi.com/trade-api/v2"
	}
	return "https://demo-api.kalshi.co/trade-api/v2"
}

func (c *Config) WSBaseURL() string {
	if c.KalshiEnv == "prod" {
		return "wss://api.elections.kalshi.com/trade-api/ws/v2"
	}
	return "wss://demo-api.kalshi.co/trade-api/ws/v2"
}

func Load() (*Config, error) {
	_ = godotenv.Load()

	cfg := &Config{
		KalshiAPIKeyID:    os.Getenv("KALSHI_API_KEY_ID"),
		KalshiPrivKeyPath: getEnvDefault("KALSHI_PRIV_KEY_PATH", "./kalshi_private_key.pem"),
		KalshiEnv:         getEnvDefault("KALSHI_ENV", "prod"),
		OutputDir:         getEnvDefault("OUTPUT_DIR", "./data"),
		SeriesTicker:      getEnvDefault("SERIES_TICKER", "KXBTC15M"),
	}

	if cfg.KalshiAPIKeyID == "" {
		return nil, fmt.Errorf("KALSHI_API_KEY_ID is required")
	}
	if cfg.KalshiEnv != "prod" && cfg.KalshiEnv != "demo" {
		return nil, fmt.Errorf("KALSHI_ENV must be 'prod' or 'demo', got %q", cfg.KalshiEnv)
	}

	return cfg, nil
}

func getEnvDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
