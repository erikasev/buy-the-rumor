package jobs

import (
	"context"
	"errors"
	"sync"
)

var ErrNotFound = errors.New("not found")

// Store holds job records. The web service only creates and reads them.
type Store interface {
	Create(ctx context.Context, r Record) error
	Get(ctx context.Context, jobID string) (Record, error)
}

// Queue sends job messages to the worker.
type Queue interface {
	Send(ctx context.Context, m Message) error
}

// Briefs reads finished brief files by their s3_key.
type Briefs interface {
	GetBrief(ctx context.Context, key string) ([]byte, error)
}

// Memory is an in-process Store and Briefs for local development and tests.
// The AWS backend (DynamoDB + S3) implements the same interfaces.
type Memory struct {
	mu      sync.Mutex
	records map[string]Record
	briefs  map[string][]byte
}

func NewMemory() *Memory {
	return &Memory{records: map[string]Record{}, briefs: map[string][]byte{}}
}

func (m *Memory) Create(_ context.Context, r Record) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.records[r.JobID]; ok {
		return errors.New("job already exists")
	}
	m.records[r.JobID] = r
	return nil
}

func (m *Memory) Get(_ context.Context, jobID string) (Record, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.records[jobID]
	if !ok {
		return Record{}, ErrNotFound
	}
	return r, nil
}

func (m *Memory) GetBrief(_ context.Context, key string) ([]byte, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	b, ok := m.briefs[key]
	if !ok {
		return nil, ErrNotFound
	}
	return b, nil
}

// update applies a worker-side change, enforcing the contract's transitions.
func (m *Memory) update(jobID string, change func(*Record) error) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.records[jobID]
	if !ok {
		return ErrNotFound
	}
	if err := change(&r); err != nil {
		return err
	}
	m.records[jobID] = r
	return nil
}

func (m *Memory) putBrief(key string, body []byte) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.briefs[key] = body
}
