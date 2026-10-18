package jobs

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
)

// FakeWorker is a Queue for local development. Instead of sending to SQS, it
// walks the job through every stage in-process and finishes with a sample
// brief built from the job's own sector and risk level, so the UI can be built
// and demoed without AWS or LLM credit.
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
	view := SampleView(m, created)
	key := fmt.Sprintf("briefs/%s/%s.md", created.Format("2006/01/02"), m.JobID)
	viewJSON, _ := json.Marshal(view)
	f.Mem.putBrief(key, []byte(SampleMarkdown(view)))
	f.Mem.putBrief(ViewKey(key), viewJSON)
	_ = f.transition(m.JobID, StatusSucceeded, func(r *Record) { r.S3Key = key })
}

// SampleView builds a placeholder brief whose limits follow the job's risk level,
// using the same scale as the UI: 10–40% max per position, 4–12% stop-loss.
// Asset names are placeholders; real briefs come from the agent pipeline.
func SampleView(m Message, generated time.Time) BriefView {
	risk := float64(m.RiskLevel)
	maxPos := math.Round(10 + risk*0.3)
	stop := math.Round(4 + risk*0.08)
	return BriefView{
		SchemaVersion: SchemaVersion,
		BriefID:       "br_local_" + strings.ToLower(m.JobID[len(m.JobID)-8:]),
		Theme:         m.Theme,
		RiskLevel:     m.RiskLevel,
		GeneratedAt:   generated,
		ExpiresAt:     generated.Add(6 * time.Hour),
		Constraints: Constraints{
			MaxTotalUSD:       100,
			MaxPositionPct:    maxPos,
			AllowedOrderTypes: []string{"limit"},
			StopLossPct:       stop,
		},
		Positions: []Position{
			{Asset: "ASSET-A-USD", Action: "buy", AllocationPct: math.Round(maxPos * 0.8), OrderType: "limit", Entry: "limit at or below 2% under last price", Confidence: "medium"},
			{Asset: "ASSET-B-USD", Action: "buy", AllocationPct: math.Round(maxPos * 0.5), OrderType: "limit", Entry: "limit at last price", Confidence: "low"},
			{Asset: "ASSET-C-USD", Action: "hold", AllocationPct: 0, Confidence: "low"},
		},
		Thesis: "Sample strategy for local development. In production, the strategist agent explains here, in plain language, " +
			"why these positions fit the " + m.Theme + " sector at this risk level.",
		Risks: "Sample text. In production, the risk reviewer agent lists what could go wrong and what would make the plan wrong.",
	}
}

// SampleMarkdown renders a view as a brief file in the format from design.md.
func SampleMarkdown(v BriefView) string {
	num := func(f float64) string { return strconv.FormatFloat(f, 'f', -1, 64) }
	var b strings.Builder
	fmt.Fprintf(&b, "---\nbrief_id: %s\nschema_version: %d\ntheme: %q\n", v.BriefID, v.SchemaVersion, v.Theme)
	fmt.Fprintf(&b, "generated_at: %s\nexpires_at: %s\n", v.GeneratedAt.Format(time.RFC3339), v.ExpiresAt.Format(time.RFC3339))
	fmt.Fprintf(&b, "constraints:\n  max_total_usd: %s\n  max_position_pct: %s\n  allowed_order_types: [%s]\n  stop_loss_pct: %s\n",
		num(v.Constraints.MaxTotalUSD), num(v.Constraints.MaxPositionPct), strings.Join(v.Constraints.AllowedOrderTypes, ", "), num(v.Constraints.StopLossPct))
	b.WriteString("positions:\n")
	for _, p := range v.Positions {
		fmt.Fprintf(&b, "  - asset: %s\n    action: %s\n    allocation_pct: %s\n", p.Asset, p.Action, num(p.AllocationPct))
		if p.OrderType != "" {
			fmt.Fprintf(&b, "    order_type: %s\n", p.OrderType)
		}
		if p.Entry != "" {
			fmt.Fprintf(&b, "    entry: %s\n", p.Entry)
		}
		fmt.Fprintf(&b, "    confidence: %s\n", p.Confidence)
	}
	b.WriteString("---\n\n# Instructions for the trading agent\n")
	fmt.Fprintf(&b, "Follow only the frontmatter. Do not trade after expires_at.\nUse %s orders only. Exit any position down %s%% from entry.\n\n",
		strings.Join(v.Constraints.AllowedOrderTypes, " or "), num(v.Constraints.StopLossPct))
	fmt.Fprintf(&b, "## Thesis\n%s\n\n## Risks and what would change the plan\n%s\n", v.Thesis, v.Risks)
	return b.String()
}
