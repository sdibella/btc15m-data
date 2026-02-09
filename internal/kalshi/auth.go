package kalshi

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/gw/btc15m-data/internal/config"
)

func LoadPrivateKey(path string) (*rsa.PrivateKey, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading private key: %w", err)
	}

	block, _ := pem.Decode(data)
	if block == nil {
		return nil, fmt.Errorf("no PEM block found in %s", path)
	}

	// Try PKCS8 first (standard format)
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err == nil {
		rsaKey, ok := key.(*rsa.PrivateKey)
		if !ok {
			return nil, fmt.Errorf("private key is not RSA")
		}
		return rsaKey, nil
	}

	// Fallback to PKCS1 (older RSA-specific format)
	rsaKey, err := x509.ParsePKCS1PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parsing private key (tried PKCS8 and PKCS1): %w", err)
	}

	return rsaKey, nil
}

func Sign(privateKey *rsa.PrivateKey, timestampMS string, method string, path string) (string, error) {
	message := timestampMS + method + path
	hash := sha256.Sum256([]byte(message))

	sig, err := rsa.SignPSS(rand.Reader, privateKey, crypto.SHA256, hash[:], &rsa.PSSOptions{
		SaltLength: rsa.PSSSaltLengthEqualsHash,
	})
	if err != nil {
		return "", fmt.Errorf("signing: %w", err)
	}

	return base64.StdEncoding.EncodeToString(sig), nil
}

func AuthHeaders(cfg *config.Config, privateKey *rsa.PrivateKey, method string, path string) (map[string]string, error) {
	ts := strconv.FormatInt(time.Now().UnixMilli(), 10)

	sig, err := Sign(privateKey, ts, method, path)
	if err != nil {
		return nil, err
	}

	return map[string]string{
		"KALSHI-ACCESS-KEY":       cfg.KalshiAPIKeyID,
		"KALSHI-ACCESS-TIMESTAMP": ts,
		"KALSHI-ACCESS-SIGNATURE": sig,
	}, nil
}
