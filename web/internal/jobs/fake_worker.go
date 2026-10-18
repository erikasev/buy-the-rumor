package jobs

import (
	"context"
	_ "embed"
	"fmt"
	"strings"
	"time"
)

//go:embed sample/brief.md
var sampleBrief []byte

// FakeWorker is a Queue for local development. Instead of sending to SQS, it
// walks the job through every stage in-process and finishes with a sample
// brief, so the UI can be built and demoed without AWS or LLM credit.
// A theme containing "fail" ends in FAILED, to exercise the error view.
type FakeWorker struct {
	Mem  *Memory
	Step time.Duration // pause per stage
	Now  func() time.Time
}

func (f *FakeWorker) Send(_ context.Context, m Message) error {
	go f.run(m)
	return nil
}

func (f *FakeWorker) now() time.Time {
	if f.Now != nil {
		return f.Now().UTC().Truncate(time.Second)
	}
	return time.Now().UTC().Truncate(time.Second)
}

func (f *FakeWorker) transition(id string, to Status, edit func(*Record)) error {
	return f.Mem.update(id, func(r *Record) error {
		if r.Status != to && !CanTransition(r.Status, to) {
			return fmt.Errorf("illegal transition %s -> %s", r.Status, to)
		}
		r.Status = to
		r.UpdatedAt = f.now()
		if edit != nil {
			edit(r)
		}
		return nil
	})
}

func (f *FakeWorker) run(m Message) {
	fail := strings.Contains(strings.ToLower(m.Theme), "fail")
	for _, stage := range Stages {
		time.Sleep(f.Step)
		if err := f.transition(m.JobID, StatusRunning, func(r *Record) { r.Stage = stage }); err != nil {
			return
		}
		if fail && stage == StageReview {
			_ = f.transition(m.JobID, StatusFailed, func(r *Record) {
				r.Error = &JobError{
					Stage:   StageReview,
					Code:    "REVIEW_REJECTED",
					Message: "The risk reviewer rejected the strategy twice.",
				}
			})
			return
		}
	}
	created := f.now()
	if r, err := f.Mem.Get(context.Background(), m.JobID); err == nil {
		created = r.CreatedAt
	}
	key := fmt.Sprintf("briefs/%s/%s.md", created.Format("2006/01/02"), m.JobID)
	f.Mem.putBrief(key, sampleBrief)
	_ = f.transition(m.JobID, StatusSucceeded, func(r *Record) { r.S3Key = key })
}
