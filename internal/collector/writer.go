package collector

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Writer is a daily-rotating JSONL file writer.
type Writer struct {
	dir      string
	prefix   string
	mu       sync.Mutex
	file     *os.File
	fileDate string // "2006-01-02" of current file
}

func NewWriter(dir, prefix string) (*Writer, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("creating output dir: %w", err)
	}
	return &Writer{dir: dir, prefix: prefix}, nil
}

func (w *Writer) Write(event any) error {
	data, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshaling event: %w", err)
	}
	data = append(data, '\n')

	w.mu.Lock()
	defer w.mu.Unlock()

	if err := w.ensureFile(); err != nil {
		return err
	}

	_, err = w.file.Write(data)
	return err
}

func (w *Writer) ensureFile() error {
	today := time.Now().UTC().Format("2006-01-02")
	if w.file != nil && w.fileDate == today {
		return nil
	}

	// Capture path before closing for background compression
	var prevPath string
	if w.file != nil {
		prevPath = w.file.Name()
		w.file.Close()
	}

	path := filepath.Join(w.dir, fmt.Sprintf("%s-%s.jsonl", w.prefix, today))
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("opening output file: %w", err)
	}

	w.file = f
	w.fileDate = today

	if prevPath != "" {
		go compressFile(prevPath)
	}

	return nil
}

func (w *Writer) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.file != nil {
		return w.file.Close()
	}
	return nil
}

// compressFile gzips a JSONL file and removes the original.
// Writes to .gz.tmp first, then renames atomically.
func compressFile(srcPath string) {
	dstPath := srcPath + ".gz"
	tmpPath := dstPath + ".tmp"

	// If .gz already exists, just clean up the original
	if _, err := os.Stat(dstPath); err == nil {
		if _, err := os.Stat(srcPath); err == nil {
			slog.Info("gzip exists, removing original", "path", srcPath)
			os.Remove(srcPath)
		}
		return
	}
	if _, err := os.Stat(srcPath); os.IsNotExist(err) {
		return
	}

	slog.Info("compressing", "src", srcPath)

	src, err := os.Open(srcPath)
	if err != nil {
		slog.Error("compress: open source", "err", err, "path", srcPath)
		return
	}
	defer src.Close()

	tmp, err := os.Create(tmpPath)
	if err != nil {
		slog.Error("compress: create tmp", "err", err, "path", tmpPath)
		return
	}

	gz, _ := gzip.NewWriterLevel(tmp, gzip.BestCompression)
	if _, err := io.Copy(gz, src); err != nil {
		gz.Close()
		tmp.Close()
		os.Remove(tmpPath)
		slog.Error("compress: copy", "err", err, "path", srcPath)
		return
	}
	if err := gz.Close(); err != nil {
		tmp.Close()
		os.Remove(tmpPath)
		slog.Error("compress: gzip close", "err", err, "path", srcPath)
		return
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpPath)
		slog.Error("compress: tmp close", "err", err, "path", srcPath)
		return
	}

	// Atomic rename
	if err := os.Rename(tmpPath, dstPath); err != nil {
		os.Remove(tmpPath)
		slog.Error("compress: rename", "err", err, "path", srcPath)
		return
	}

	// Remove original
	if err := os.Remove(srcPath); err != nil {
		slog.Warn("compress: remove original", "err", err, "path", srcPath)
		return
	}

	slog.Info("compressed", "dst", dstPath)
}

// CompressStaleFiles compresses any JSONL files from previous days.
// Call on startup to handle files left uncompressed after a crash.
func CompressStaleFiles(dir, prefix string) {
	today := time.Now().UTC().Format("2006-01-02")

	// Clean up leftover .gz.tmp files
	tmps, _ := filepath.Glob(filepath.Join(dir, prefix+"-*.jsonl.gz.tmp"))
	for _, tmp := range tmps {
		slog.Warn("removing stale tmp", "path", tmp)
		os.Remove(tmp)
	}

	// Find JSONL files from previous days
	pattern := filepath.Join(dir, prefix+"-*.jsonl")
	files, _ := filepath.Glob(pattern)
	for _, f := range files {
		base := filepath.Base(f)
		// Extract date from prefix-YYYY-MM-DD.jsonl
		dateStr := strings.TrimPrefix(base, prefix+"-")
		dateStr = strings.TrimSuffix(dateStr, ".jsonl")
		if dateStr == today {
			continue
		}
		go compressFile(f)
	}
}
