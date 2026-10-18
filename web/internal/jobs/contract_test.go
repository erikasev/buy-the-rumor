package jobs

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

// Shared with the Python worker tests: both sides must agree on these files.
var fixtures = filepath.Join("..", "..", "..", "contracts", "fixtures")

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(fixtures, name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestValidMessageFixtureParses(t *testing.T) {
	m, err := ParseMessage(readFixture(t, "message.valid.json"))
	if err != nil {
		t.Fatal(err)
	}
	if m.Theme != "AI and compute" {
		t.Errorf("theme = %q", m.Theme)
	}
}

func TestMessageMissingThemeIsRejected(t *testing.T) {
	if _, err := ParseMessage(readFixture(t, "message.missing_theme.json")); err == nil {
		t.Fatal("expected an error")
	}
}

func TestUnknownMessageFieldsAreIgnored(t *testing.T) {
	b := bytes.Replace(readFixture(t, "message.valid.json"), []byte("{"), []byte(`{"added_later": true,`), 1)
	if _, err := ParseMessage(b); err != nil {
		t.Fatal(err)
	}
}

// The message we send must use exactly the contract's field names.
func TestOutgoingMessageMatchesFixtureFields(t *testing.T) {
	rec := NewRecord("01JADQ7Y3K8V5C2N9W4R6T1M0P", "AI and compute", 40, time.Date(2026, 10, 21, 13, 0, 0, 0, time.UTC))
	ours, _ := json.Marshal(MessageFor(rec))
	if got, want := keys(t, ours), keys(t, readFixture(t, "message.valid.json")); got != want {
		t.Errorf("fields = %s, want %s", got, want)
	}
	if !bytes.Contains(ours, []byte(`"requested_at":"2026-10-21T13:00:00Z"`)) {
		t.Errorf("timestamp not in contract format: %s", ours)
	}
}

func TestRecordFixturesDecode(t *testing.T) {
	for _, name := range []string{"record.queued.json", "record.succeeded.json", "record.failed.json"} {
		var r Record
		if err := json.Unmarshal(readFixture(t, name), &r); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		switch r.Status {
		case StatusQueued:
		case StatusSucceeded:
			if !strings.HasSuffix(r.S3Key, r.JobID+".md") {
				t.Errorf("%s: s3_key = %q", name, r.S3Key)
			}
		case StatusFailed:
			if r.Error == nil || r.Error.Message == "" {
				t.Errorf("%s: missing error", name)
			}
		default:
			t.Errorf("%s: unexpected status %q", name, r.Status)
		}
	}
}

func TestNewRecordMatchesQueuedFixture(t *testing.T) {
	rec := NewRecord("01JADQ7Y3K8V5C2N9W4R6T1M0P", "AI and compute", 40, time.Date(2026, 10, 21, 13, 0, 0, 0, time.UTC))
	ours, _ := json.Marshal(rec)
	if got, want := keys(t, ours), keys(t, readFixture(t, "record.queued.json")); got != want {
		t.Errorf("fields = %s, want %s", got, want)
	}
	var fixture Record
	_ = json.Unmarshal(readFixture(t, "record.queued.json"), &fixture)
	if rec.TTL != fixture.TTL {
		t.Errorf("ttl = %d, want %d (created_at + 30 days)", rec.TTL, fixture.TTL)
	}
}

func TestRiskLevelOutOfRangeIsRejected(t *testing.T) {
	for _, bad := range []string{"-1", "101"} {
		b := bytes.Replace(readFixture(t, "message.valid.json"), []byte(`"risk_level": 40`), []byte(`"risk_level": `+bad), 1)
		if _, err := ParseMessage(b); err == nil {
			t.Errorf("risk_level %s should be rejected", bad)
		}
	}
}

func TestTransitions(t *testing.T) {
	cases := []struct {
		from, to Status
		ok       bool
	}{
		{StatusQueued, StatusRunning, true},
		{StatusQueued, StatusFailed, true},
		{StatusRunning, StatusSucceeded, true},
		{StatusRunning, StatusFailed, true},
		{StatusSucceeded, StatusRunning, false},
		{StatusFailed, StatusQueued, false},
		{StatusQueued, StatusSucceeded, false},
	}
	for _, c := range cases {
		if got := CanTransition(c.from, c.to); got != c.ok {
			t.Errorf("%s -> %s = %v, want %v", c.from, c.to, got, c.ok)
		}
	}
}

func TestCleanTheme(t *testing.T) {
	if got, err := CleanTheme("  AI   and\tcompute "); err != nil || got != "AI and compute" {
		t.Errorf("got %q, %v", got, err)
	}
	for _, bad := range []string{"", "   ", strings.Repeat("x", 101)} {
		if _, err := CleanTheme(bad); err == nil {
			t.Errorf("CleanTheme(%q) should fail", bad)
		}
	}
	if _, err := CleanTheme(strings.Repeat("é", 100)); err != nil {
		t.Errorf("100 multibyte characters should be allowed: %v", err)
	}
}

func keys(t *testing.T, b []byte) string {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ",")
}
