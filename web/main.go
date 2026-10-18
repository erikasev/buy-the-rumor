// Command web runs the Buy the Rumor web service: the UI and the HTTP API.
//
// Configuration comes from environment variables:
//
//	PORT          port to listen on (default 8080)
//	BTR_BACKEND   "memory" (default): in-process store and a fake worker, no AWS needed
//	              "aws": DynamoDB, SQS and S3, using the job contract's env vars
//	BTR_FAKE_STEP pause per stage for the fake worker (default 1s)
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/erikasev/buy-the-rumor/web/internal/jobs"
	"github.com/erikasev/buy-the-rumor/web/internal/web"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	srv := &web.Server{Log: log}

	switch backend := envOr("BTR_BACKEND", "memory"); backend {
	case "memory":
		step, err := time.ParseDuration(envOr("BTR_FAKE_STEP", "1s"))
		if err != nil {
			return err
		}
		mem := jobs.NewMemory()
		srv.Store, srv.Briefs = mem, mem
		srv.Queue = &jobs.FakeWorker{Mem: mem, Step: step}
		log.Warn("using in-memory backend with a fake worker; nothing is persisted")
	case "aws":
		return errors.New("aws backend is not implemented yet")
	default:
		return errors.New("BTR_BACKEND must be memory or aws, got " + backend)
	}

	httpSrv := &http.Server{
		Addr:              ":" + envOr("PORT", "8080"),
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// Shut down cleanly when Beanstalk or Docker sends SIGTERM.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		log.Info("listening", "addr", httpSrv.Addr)
		errCh <- httpSrv.ListenAndServe()
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return httpSrv.Shutdown(shutdownCtx)
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
