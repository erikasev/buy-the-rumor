package jobs

import (
	"strings"
	"time"
)

// BriefView is the structured form of a brief (docs/contracts/job-v1.md, "Brief view").
// The worker writes it as <job_id>.json beside <job_id>.md; the web service renders
// the brief page from it. Field names match the brief's YAML frontmatter.
type BriefView struct {
	SchemaVersion int         `json:"schema_version"`
	BriefID       string      `json:"brief_id"`
	Theme         string      `json:"theme"`
	RiskLevel     int         `json:"risk_level"`
	GeneratedAt   time.Time   `json:"generated_at"`
	ExpiresAt     time.Time   `json:"expires_at"`
	Constraints   Constraints `json:"constraints"`
	Positions     []Position  `json:"positions"`
	Thesis        string      `json:"thesis"`
	Risks         string      `json:"risks"`
}

type Constraints struct {
	MaxTotalUSD       float64  `json:"max_total_usd"`
	MaxPositionPct    float64  `json:"max_position_pct"`
	AllowedOrderTypes []string `json:"allowed_order_types"`
	StopLossPct       float64  `json:"stop_loss_pct"`
}

type Position struct {
	Asset         string  `json:"asset"`
	Action        string  `json:"action"`
	AllocationPct float64 `json:"allocation_pct"`
	OrderType     string  `json:"order_type,omitempty"`
	Entry         string  `json:"entry,omitempty"`
	Confidence    string  `json:"confidence"`
}

// ViewKey returns the S3 key of the brief view that sits beside a brief's .md key.
func ViewKey(mdKey string) string { return strings.TrimSuffix(mdKey, ".md") + ".json" }
