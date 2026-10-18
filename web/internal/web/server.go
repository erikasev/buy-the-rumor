// Package web serves the UI and the HTTP API (design.md, "Request flow and API").
// Responses for htmx are HTML fragments.
package web

import (
	"embed"
	"errors"
	"html/template"
	"io/fs"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/erikasev/buy-the-rumor/web/internal/jobs"
)

//go:embed templates/*.html
var templateFS embed.FS

//go:embed static
var staticFS embed.FS

// Friendly text for each stage. The contract leaves this wording to the web service.
var stageLabels = map[jobs.Stage]string{
	jobs.StageUniverse:     "Finding tradable assets",
	jobs.StageThemeMapping: "Matching assets to your theme",
	jobs.StageCollecting:   "Gathering prices and news",
	jobs.StageSentiment:    "Reading the market mood",
	jobs.StageStrategy:     "Drafting positions",
	jobs.StageReview:       "Risk review",
	jobs.StageValidation:   "Checking hard limits",
}

type Server struct {
	Store  jobs.Store
	Queue  jobs.Queue
	Briefs jobs.Briefs
	Log    *slog.Logger
	Now    func() time.Time
	NewID  func(time.Time) (string, error)

	tmpl *template.Template
}

// Handler returns the routed, middleware-wrapped HTTP handler.
func (s *Server) Handler() http.Handler {
	if s.Now == nil {
		s.Now = time.Now
	}
	if s.NewID == nil {
		s.NewID = jobs.NewULID
	}
	if s.Log == nil {
		s.Log = slog.Default()
	}
	s.tmpl = template.Must(template.ParseFS(templateFS, "templates/*.html"))

	static, err := fs.Sub(staticFS, "static")
	if err != nil {
		panic(err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", s.index)
	mux.HandleFunc("GET /healthz", s.healthz)
	mux.HandleFunc("POST /briefs", s.createBrief)
	mux.HandleFunc("GET /briefs/{id}", s.getBrief)
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServerFS(static)))
	return securityHeaders(mux)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy",
			"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "+
				"frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		next.ServeHTTP(w, r)
	})
}

func (s *Server) healthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	_, _ = w.Write([]byte("ok\n"))
}

func (s *Server) index(w http.ResponseWriter, _ *http.Request) {
	s.render(w, http.StatusOK, "index.html", nil)
}

func (s *Server) createBrief(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 4<<10)
	if err := r.ParseForm(); err != nil {
		s.renderError(w, http.StatusBadRequest, "Could not read the form.")
		return
	}
	theme, err := jobs.CleanTheme(r.PostFormValue("theme"))
	if err != nil {
		s.renderError(w, http.StatusBadRequest, "Enter a theme between 1 and 100 characters.")
		return
	}

	risk := jobs.DefaultRiskLevel
	if raw := r.PostFormValue("risk_level"); raw != "" {
		v, err := strconv.Atoi(raw)
		if err != nil || !jobs.ValidRisk(v) {
			s.renderError(w, http.StatusBadRequest, "Risk level must be between 0 and 100.")
			return
		}
		risk = v
	}

	now := s.Now()
	id, err := s.NewID(now)
	if err != nil {
		s.fail(w, "new job id", err)
		return
	}
	rec := jobs.NewRecord(id, theme, risk, now)

	// Write the record before sending the message, so the worker never sees a
	// job that doesn't exist yet. If the send fails, the orphaned QUEUED record
	// expires through its TTL.
	if err := s.Store.Create(r.Context(), rec); err != nil {
		s.fail(w, "create record", err)
		return
	}
	if err := s.Queue.Send(r.Context(), jobs.MessageFor(rec)); err != nil {
		s.fail(w, "send message", err)
		return
	}
	s.Log.Info("job_created", "job_id", id)
	s.render(w, http.StatusAccepted, "status.html", s.statusView(r, rec))
}

func (s *Server) getBrief(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	download := strings.HasSuffix(id, ".md")
	id = strings.TrimSuffix(id, ".md")
	if !jobs.ValidJobID(id) {
		http.NotFound(w, r)
		return
	}
	rec, err := s.Store.Get(r.Context(), id)
	if errors.Is(err, jobs.ErrNotFound) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		s.fail(w, "get record", err)
		return
	}

	if download {
		if rec.Status != jobs.StatusSucceeded {
			http.NotFound(w, r)
			return
		}
		body, err := s.Briefs.GetBrief(r.Context(), rec.S3Key)
		if err != nil {
			s.fail(w, "get brief", err)
			return
		}
		w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
		w.Header().Set("Content-Disposition", `attachment; filename="`+id+`.md"`)
		_, _ = w.Write(body)
		return
	}

	s.render(w, http.StatusOK, "status.html", s.statusView(r, rec))
}

type statusView struct {
	ID         string
	Theme      string
	RiskLevel  int
	RiskWord   string
	Status     jobs.Status
	Polling    bool
	StageLabel string
	StepNum    int
	StepTotal  int
	Brief      string
	Error      string
}

func (s *Server) statusView(r *http.Request, rec jobs.Record) statusView {
	v := statusView{
		ID:        rec.JobID,
		Theme:     rec.Theme,
		RiskLevel: rec.RiskLevel,
		RiskWord:  riskWord(rec.RiskLevel),
		Status:    rec.Status,
		Polling:   !rec.Status.Done(),
		StepTotal: len(jobs.Stages),
	}
	for i, st := range jobs.Stages {
		if st == rec.Stage {
			v.StepNum = i + 1
		}
	}
	v.StageLabel = stageLabels[rec.Stage]
	switch rec.Status {
	case jobs.StatusSucceeded:
		body, err := s.Briefs.GetBrief(r.Context(), rec.S3Key)
		if err != nil {
			s.Log.Error("get_brief_failed", "job_id", rec.JobID, "err", err)
			v.Error = "The brief is ready but could not be loaded. Try again shortly."
		} else {
			v.Brief = string(body)
		}
	case jobs.StatusFailed:
		v.Error = "This brief could not be generated."
		if rec.Error != nil && rec.Error.Message != "" {
			v.Error = rec.Error.Message
		}
	}
	return v
}

// riskWord matches the slider's three bands in the UI.
func riskWord(r int) string {
	switch {
	case r <= 33:
		return "Cautious"
	case r <= 66:
		return "Balanced"
	default:
		return "Bold"
	}
}

func (s *Server) render(w http.ResponseWriter, code int, name string, data any) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(code)
	if err := s.tmpl.ExecuteTemplate(w, name, data); err != nil {
		s.Log.Error("render_failed", "template", name, "err", err)
	}
}

func (s *Server) renderError(w http.ResponseWriter, code int, msg string) {
	s.render(w, code, "error.html", msg)
}

// fail logs the real error and shows the user a generic message.
func (s *Server) fail(w http.ResponseWriter, op string, err error) {
	s.Log.Error("request_failed", "op", op, "err", err)
	s.renderError(w, http.StatusServiceUnavailable, "Something went wrong. Please try again.")
}
