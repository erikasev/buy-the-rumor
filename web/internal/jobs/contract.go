// Package jobs holds the job contract types (docs/contracts/job-v1.md) and the
// storage and queue interfaces the web service depends on.
//
// If this file and the contract document disagree, the document wins.
package jobs

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const SchemaVersion = 1

// TTL is how long a job record lives before DynamoDB deletes it.
const TTL = 30 * 24 * time.Hour

const MaxThemeLen = 100

type Status string

const (
	StatusQueued    Status = "QUEUED"
	StatusRunning   Status = "RUNNING"
	StatusSucceeded Status = "SUCCEEDED"
	StatusFailed    Status = "FAILED"
)

// Done reports whether the job has reached a final status.
func (s Status) Done() bool { return s == StatusSucceeded || s == StatusFailed }

type Stage string

const (
	StageUniverse     Stage = "universe"
	StageThemeMapping Stage = "theme_mapping"
	StageCollecting   Stage = "collecting"
	StageSentiment    Stage = "sentiment"
	StageStrategy     Stage = "strategy"
	StageReview       Stage = "review"
	StageValidation   Stage = "validation"
)

// Stages lists the pipeline stages in order.
var Stages = []Stage{
	StageUniverse, StageThemeMapping, StageCollecting, StageSentiment,
	StageStrategy, StageReview, StageValidation,
}

var transitions = map[Status][]Status{
	StatusQueued:  {StatusRunning, StatusFailed},
	StatusRunning: {StatusSucceeded, StatusFailed},
}

// CanTransition reports whether the contract allows moving from one status to another.
func CanTransition(from, to Status) bool {
	for _, s := range transitions[from] {
		if s == to {
			return true
		}
	}
	return false
}

// Message is the SQS message the web service sends to the worker.
type Message struct {
	SchemaVersion int       `json:"schema_version"`
	JobID         string    `json:"job_id"`
	Theme         string    `json:"theme"`
	RequestedAt   time.Time `json:"requested_at"`
}

// JobError is the `error` map on a FAILED record. Message is safe to show users.
type JobError struct {
	Stage   Stage  `json:"stage"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Record is the DynamoDB job record.
type Record struct {
	JobID         string    `json:"job_id"`
	SchemaVersion int       `json:"schema_version"`
	Theme         string    `json:"theme"`
	Status        Status    `json:"status"`
	Stage         Stage     `json:"stage,omitempty"`
	CreatedAt     time.Time `json:"created_at"`
	UpdatedAt     time.Time `json:"updated_at"`
	S3Key         string    `json:"s3_key,omitempty"`
	Error         *JobError `json:"error,omitempty"`
	TTL           int64     `json:"ttl"`
}

var ErrInvalidTheme = errors.New("theme must be 1 to 100 characters")

// CleanTheme trims a user-entered theme and checks its length.
func CleanTheme(raw string) (string, error) {
	theme := strings.Join(strings.Fields(raw), " ")
	if n := utf8.RuneCountInString(theme); n < 1 || n > MaxThemeLen {
		return "", ErrInvalidTheme
	}
	return theme, nil
}

// NewRecord builds the QUEUED record the web service writes for a new job.
func NewRecord(jobID, theme string, now time.Time) Record {
	now = now.UTC().Truncate(time.Second)
	return Record{
		JobID:         jobID,
		SchemaVersion: SchemaVersion,
		Theme:         theme,
		Status:        StatusQueued,
		CreatedAt:     now,
		UpdatedAt:     now,
		TTL:           now.Add(TTL).Unix(),
	}
}

// MessageFor builds the SQS message for a newly created record.
func MessageFor(r Record) Message {
	return Message{
		SchemaVersion: SchemaVersion,
		JobID:         r.JobID,
		Theme:         r.Theme,
		RequestedAt:   r.CreatedAt,
	}
}

var ulidRe = regexp.MustCompile(`^[0-7][0-9A-HJKMNP-TV-Z]{25}$`)

// ValidJobID reports whether id looks like a ULID.
func ValidJobID(id string) bool { return ulidRe.MatchString(id) }

// Validate checks a message against the contract.
func (m Message) Validate() error {
	switch {
	case m.SchemaVersion != SchemaVersion:
		return fmt.Errorf("unsupported schema_version %d", m.SchemaVersion)
	case !ValidJobID(m.JobID):
		return errors.New("job_id is not a ULID")
	case m.RequestedAt.IsZero():
		return errors.New("requested_at is required")
	}
	if _, err := CleanTheme(m.Theme); err != nil {
		return err
	}
	return nil
}

// ParseMessage decodes and validates a message. Unknown fields are ignored.
func ParseMessage(data []byte) (Message, error) {
	var m Message
	if err := json.Unmarshal(data, &m); err != nil {
		return Message{}, err
	}
	return m, m.Validate()
}
