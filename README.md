# Buy the Rumor

Buy the Rumor turns a trader's area of interest into a **research brief**: a short list of leads worth investigating, drawn from what the market is saying before it becomes headline news. The name comes from "buy the rumor, sell the news."

The brief is written for another AI agent (or a person) to act on. It doesn't place trades. It tells the next agent what to look into, why, how an idea could be expressed, and which rules it must stay within.

> Research and decision support only, not investment advice. Leads are hypotheses drawn from unverified market commentary and must be checked with real data before anyone acts on them.

## How it works

1. **The user describes what they want**, in plain words: a theme or sector, the instruments they trade, position sizes, how often they trade, their horizon, and a risk level from cautious to bold.
2. **Their rules are read out of the request**, e.g. "stocks and options only, max 10% per position". These rules are strict, and they carry over into the brief unchanged.
3. **Relevant market commentary is gathered** from a curated, private set of sources and cached so it isn't fetched twice.
4. **An LLM turns the commentary into research leads.** Each lead has:
   - the **signal** observed (e.g. credit spreads widening sharply);
   - the **hypothesis** it suggests;
   - concrete **research steps** to verify it;
   - possible **ways to express it**, using only instruments the user allows (e.g. put options, if options are permitted);
   - a **horizon**, a **conviction** level, and what would **invalidate** it.
5. **Code checks the result before anything is saved.** A brief that fails a check is never delivered.

## Safety by design

- **Hard limits live in code, not prompts.** Fixed caps on budget, position size, order type and stop-loss apply to every brief. The user's own numbers can tighten them, never loosen them. A suggestion that uses an instrument the user ruled out is rejected, even if it's mislabeled.
- **Every lead must be grounded.** Each lead has to cite real collected evidence, which code checks. A lead with no evidence, or that cites something that doesn't exist, is rejected.
- **Sources stay internal.** Evidence is kept privately for traceability. Nothing users receive names a source (see [ADR-0001](docs/adr/0001-data-sources-stay-internal.md)).
- **Fetched text is treated as data, never as instructions.** This guards against prompt injection hidden in the commentary.
- **Briefs expire**, so stale sentiment isn't acted on.
- **The model's output is structured** (JSON checked against a schema) and gets one retry before the job fails with a clear reason.

## Architecture

```
Browser ──► Web service (Go + htmx) ──► Job queue (SQS) ──► Worker (Python on AWS Lambda)
                  │                                              │
                  └──────── reads job status and briefs ◄────────┴─ writes to DynamoDB + S3
```

- **Web service** (`web/`): Go standard library with server-rendered templates and htmx. It creates jobs and shows their status and the finished brief. It never runs the agents itself.
- **Worker** (`worker/`): the agent pipeline, packaged as a Lambda container image and triggered by the queue. Each job ends as either a validated brief or a failure with a clear reason, never a partial result.
- **LLMs** are called through [OpenRouter](https://openrouter.ai), with a cheaper model for small steps and a stronger one for writing the leads. Model choices are configuration, not code.
- **Storage**: DynamoDB holds job status, and a private S3 bucket holds briefs and evidence.

Each finished brief comes in three forms: a Markdown file for the next agent, a structured JSON view for the web page, and a private evidence file for audit.

## Repository layout

```
web/        Go web service (UI and API)
worker/     Python agent pipeline for Lambda
contracts/  Shared fixtures for the web/worker job contract
docs/       Design doc, job contract, and architecture decision records (ADRs)
```

- [docs/design.md](docs/design.md): system design
- [docs/contracts/job-v1.md](docs/contracts/job-v1.md): the contract between the web service and the worker
- [docs/adr/](docs/adr/): key decisions, including [briefs as research leads](docs/adr/0002-briefs-are-research-leads.md)

## Development

Each service has its own `AGENTS.md` with full setup, commands and rules: [web/AGENTS.md](web/AGENTS.md) and [worker/AGENTS.md](worker/AGENTS.md).

```bash
# Web service, with an in-memory backend and a fake worker (no AWS needed)
cd web && go run .                      # http://localhost:8080
go test -race ./...

# Worker
cd worker
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install -r requirements-dev.txt
pytest -q && ruff check . && ruff format --check .

# Worker Lambda image (build from the repo root)
docker build -f worker/Dockerfile -t btr-worker .
```

Secrets (API keys) are never committed. In production they're Lambda environment variables; for local runs they go in an untracked `.env.local`.
