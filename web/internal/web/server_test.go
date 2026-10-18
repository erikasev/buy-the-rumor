package web

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/erikasev/buy-the-rumor/web/internal/jobs"
)

const fixedID = "01JADQ7Y3K8V5C2N9W4R6T1M0P"

// recordingQueue captures messages instead of sending them.
type recordingQueue struct{ sent []jobs.Message }

func (q *recordingQueue) Send(_ context.Context, m jobs.Message) error {
	q.sent = append(q.sent, m)
	return nil
}

func newTestServer(t *testing.T) (*httptest.Server, *jobs.Memory, *recordingQueue) {
	t.Helper()
	mem := jobs.NewMemory()
	q := &recordingQueue{}
	s := &Server{
		Store:  mem,
		Queue:  q,
		Briefs: mem,
		Log:    slog.New(slog.NewTextHandler(io.Discard, nil)),
		Now:    func() time.Time { return time.Date(2026, 10, 21, 13, 0, 0, 0, time.UTC) },
		NewID:  func(time.Time) (string, error) { return fixedID, nil },
	}
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(ts.Close)
	return ts, mem, q
}

func get(t *testing.T, url string) (*http.Response, string) {
	t.Helper()
	res, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	b, _ := io.ReadAll(res.Body)
	return res, string(b)
}

func postTheme(t *testing.T, base, theme string) (*http.Response, string) {
	t.Helper()
	res, err := http.PostForm(base+"/briefs", url.Values{"theme": {theme}})
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	b, _ := io.ReadAll(res.Body)
	return res, string(b)
}

func TestHealthz(t *testing.T) {
	ts, _, _ := newTestServer(t)
	if res, _ := get(t, ts.URL+"/healthz"); res.StatusCode != http.StatusOK {
		t.Errorf("status = %d", res.StatusCode)
	}
}

func TestIndexHasFormAndSecurityHeaders(t *testing.T) {
	ts, _, _ := newTestServer(t)
	res, body := get(t, ts.URL+"/")
	if res.StatusCode != http.StatusOK || !strings.Contains(body, `hx-post="/briefs"`) {
		t.Fatalf("status = %d, form missing", res.StatusCode)
	}
	if res.Header.Get("Content-Security-Policy") == "" || res.Header.Get("X-Content-Type-Options") != "nosniff" {
		t.Error("security headers missing")
	}
}

func TestStaticHtmxIsServed(t *testing.T) {
	ts, _, _ := newTestServer(t)
	if res, _ := get(t, ts.URL+"/static/htmx.min.js"); res.StatusCode != http.StatusOK {
		t.Errorf("status = %d", res.StatusCode)
	}
}

func TestCreateBriefWritesRecordThenSendsMessage(t *testing.T) {
	ts, mem, q := newTestServer(t)
	res, body := postTheme(t, ts.URL, "  AI and compute ")
	if res.StatusCode != http.StatusAccepted {
		t.Fatalf("status = %d: %s", res.StatusCode, body)
	}
	rec, err := mem.Get(context.Background(), fixedID)
	if err != nil || rec.Status != jobs.StatusQueued || rec.Theme != "AI and compute" {
		t.Fatalf("record = %+v, err = %v", rec, err)
	}
	if len(q.sent) != 1 || q.sent[0].JobID != fixedID || q.sent[0].Validate() != nil {
		t.Fatalf("sent = %+v", q.sent)
	}
	if !strings.Contains(body, `hx-get="/briefs/`+fixedID+`"`) {
		t.Error("response should start polling")
	}
}

func TestCreateBriefRejectsBadTheme(t *testing.T) {
	ts, _, q := newTestServer(t)
	for _, theme := range []string{"", "   ", strings.Repeat("x", 101)} {
		if res, _ := postTheme(t, ts.URL, theme); res.StatusCode != http.StatusBadRequest {
			t.Errorf("theme %q: status = %d", theme, res.StatusCode)
		}
	}
	if len(q.sent) != 0 {
		t.Error("no message should be sent for a bad theme")
	}
}

func TestThemeIsHTMLEscaped(t *testing.T) {
	ts, _, _ := newTestServer(t)
	_, body := postTheme(t, ts.URL, `<script>alert(1)</script>`)
	if strings.Contains(body, "<script>alert") {
		t.Error("theme was not escaped")
	}
}

func TestUnknownOrMalformedIDIs404(t *testing.T) {
	ts, _, _ := newTestServer(t)
	for _, path := range []string{"/briefs/" + fixedID, "/briefs/not-an-id", "/briefs/" + fixedID + ".md"} {
		if res, _ := get(t, ts.URL+path); res.StatusCode != http.StatusNotFound {
			t.Errorf("%s: status = %d", path, res.StatusCode)
		}
	}
}

func TestFakeWorkerFlowEndsWithDownloadableBrief(t *testing.T) {
	mem := jobs.NewMemory()
	s := &Server{
		Store: mem, Briefs: mem, Queue: &jobs.FakeWorker{Mem: mem},
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()

	_, body := postTheme(t, ts.URL, "AI and compute")
	id := between(body, `hx-get="/briefs/`, `"`)
	if id == "" {
		t.Fatalf("no job id in response: %s", body)
	}

	var page string
	for range 50 {
		_, page = get(t, ts.URL+"/briefs/"+id)
		if strings.Contains(page, "Brief ready") {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(page, "Brief ready") || strings.Contains(page, "hx-trigger") {
		t.Fatalf("brief never finished, or still polling: %s", page)
	}

	res, md := get(t, ts.URL+"/briefs/"+id+".md")
	if res.StatusCode != http.StatusOK || !strings.HasPrefix(md, "---") {
		t.Fatalf("download status = %d", res.StatusCode)
	}
	if !strings.Contains(res.Header.Get("Content-Disposition"), id+".md") {
		t.Errorf("Content-Disposition = %q", res.Header.Get("Content-Disposition"))
	}
}

func TestFailedJobShowsErrorMessage(t *testing.T) {
	mem := jobs.NewMemory()
	s := &Server{
		Store: mem, Briefs: mem, Queue: &jobs.FakeWorker{Mem: mem},
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()

	_, body := postTheme(t, ts.URL, "please fail")
	id := between(body, `hx-get="/briefs/`, `"`)
	var page string
	for range 50 {
		_, page = get(t, ts.URL+"/briefs/"+id)
		if strings.Contains(page, "rejected") {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(page, "The risk reviewer rejected the strategy twice.") {
		t.Fatalf("error not shown: %s", page)
	}
}

func between(s, start, end string) string {
	i := strings.Index(s, start)
	if i < 0 {
		return ""
	}
	s = s[i+len(start):]
	if j := strings.Index(s, end); j >= 0 {
		return s[:j]
	}
	return ""
}
