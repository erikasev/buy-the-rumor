# AGENTS.md — web/

Instructions for AI coding agents (and humans) working in this folder.

## What this is

The Buy the Rumor web service, written in Go. It serves the UI (server-rendered `html/template` + htmx) and the HTTP API. It creates jobs and reads their status; it never runs the agents. Deployed as a Docker image on Elastic Beanstalk. See `docs/design.md` and `docs/contracts/job-v1.md`.

## Layout

```
web/
  main.go                 Config from env, wiring, graceful shutdown
  internal/jobs/          Job contract types, ULIDs, Store/Queue/Briefs interfaces,
                          in-memory backend and fake worker for local dev
  internal/web/           HTTP handlers, templates/, static/ (embedded into the binary)
    static/stage.js       The scroll intro and cube build (plain JS, no libraries)
    static/app.css        All styles; fonts are self-hosted in static/fonts/
  Dockerfile              Multi-stage build to a distroless, non-root image
```

Shared contract fixtures live at the repo root in `contracts/fixtures/`. The Python worker tests use the same files.

## Commands

Run from `web/`:

```bash
go run .                         # http://localhost:8080, in-memory backend + fake worker
BTR_FAKE_STEP=200ms go run .     # faster fake stages
go test -race ./...              # tests
go vet ./... && gofmt -l .       # gofmt -l must print nothing
govulncheck ./...                # known vulnerabilities (go install golang.org/x/vuln/cmd/govulncheck@latest)
docker build -t btr-web .        # image
docker run --rm -p 8080:8080 btr-web
```

In local mode, a theme containing "fail" ends in FAILED, to exercise the error view.

## How the page works

- `index.html` is one page: a tall scroll "intro" with a sticky stage, the command bar (an htmx form), and an empty `#brief-view`.
- The form posts to `/briefs`; htmx swaps the returned `#job` fragment into `#result`, and it polls itself every 2 s.
- `stage.js` watches `#result` with a MutationObserver and reads the `data-status` / `data-step` attributes. It never calls the API itself, so htmx stays the only network code.
- On `SUCCEEDED`, the fragment carries the brief inside a `<template id="brief-tpl">`; `stage.js` plays the opening and then moves it into `#brief-view`.
- `stage.js` changes only `opacity`, `left`/`top` and `transform` through the CSSOM. That is allowed under the CSP; inline `style=""` attributes in templates are not (a test enforces this).

## Rules

- **Standard library only.** No third-party Go modules without a written reason in the PR; minimal dependencies are a security decision (see design.md, Security). The AWS SDK, when added for the `aws` backend, is the expected exception.
- **Fonts are self-hosted:** Instrument Sans (variable, 400–700) and IBM Plex Mono (400, 500) from the Fontsource npm packages, version 5.3.0, under the SIL Open Font License (license files beside them). No Google Fonts or other CDN.
- **htmx is vendored** at `internal/web/static/htmx.min.js`. Version 2.0.11, sha256 `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`. Update it only in its own PR, from the npm package, recording the new version and hash here.
- **No inline scripts or styles.** The Content-Security-Policy blocks them. Use `/static/app.css` and htmx attributes.
- **The web service only creates and reads job records.** Status changes belong to the worker (job contract, single-writer rule).
- **Always render user-provided text through `html/template`.** Never build HTML with string concatenation.
- **Errors shown to users stay generic;** details go to the logs via `slog`.
- **Contract first.** If code and `docs/contracts/job-v1.md` disagree, fix the code or change the contract in its own reviewed PR.

## Definition of done for a PR

- `go vet`, `gofmt -l` (empty), `go test -race`, and `govulncheck` pass.
- New handlers have tests, including a failure case.
- UI changes checked in a real browser, including the scroll intro, a full run, a failed run (a theme containing "fail"), and with "reduce motion" turned on.

## Infra expectations (for whoever wires up AWS)

- Listens on `PORT` (default 8080). Health check: `GET /healthz` returns 200.
- Handles `SIGTERM` with a 10-second graceful shutdown.
- `BTR_BACKEND=aws` will read the job contract's env vars (`JOBS_QUEUE_URL`, `JOBS_TABLE_NAME`, `BRIEFS_BUCKET_NAME`, `AWS_REGION`). Not implemented yet; until then, deploy with the default `memory` backend.
- Logs are JSON lines on stdout.
