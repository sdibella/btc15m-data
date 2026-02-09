package collector

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
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

	// Rotate: close old file, open new one
	if w.file != nil {
		w.file.Close()
	}

	path := filepath.Join(w.dir, fmt.Sprintf("%s-%s.jsonl", w.prefix, today))
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("opening output file: %w", err)
	}

	w.file = f
	w.fileDate = today
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
