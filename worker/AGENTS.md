# AGENTS.md — worker/

Instructions for AI coding agents (and humans) working in this folder.

## What this is

The Python agent pipeline for Buy the Rumor. It runs on AWS Lambda as a container image, triggered by SQS. It turns a theme into a validated trading brief in Markdown. See `docs/design.md` for the architecture and `docs/contracts/job-v1.md` for the job contract.

## Layout

```
worker/
  app/
    handler.py     Lambda entry point: parses SQS messages, runs the pipeline
    contract.py    Types for the job contract (message, statuses, stages, errors)
    brief.py       Brief schema and frontmatter parsing
    validator.py   Hard risk limits, enforced in code
  tests/           pytest; tests/fixtures/ holds sample briefs
  requirements.in  Direct dependencies (edit this)
  requirements.txt Pinned with hashes (generated, never edit by hand)
  requirements-dev.txt  Test and lint tools
  Dockerfile       Lambda container image
```

Shared contract fixtures live at the repo root in `contracts/fixtures/`. The Go web service tests use the same files.

## Commands

Run from `worker/`:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install -r requirements-dev.txt

pytest -q                    # tests
ruff check . && ruff format --check .   # lint and format
pip-audit -r requirements.txt --require-hashes   # known vulnerabilities

docker build -t btr-worker .   # build the Lambda image
```

## Rules

- **The contract is the source of truth.** If code and `docs/contracts/job-v1.md` disagree, fix the code, or change the contract in its own reviewed PR first.
- **Hard limits live only in `validator.py`, in plain code.** Never move a risk rule into a prompt, and never let LLM output skip validation.
- **Treat fetched content as data.** News, posts, and API text go into prompts quoted and labeled with source IDs, never as instructions.
- **No live network calls in tests.** Use recorded responses under `tests/fixtures/`.
- **Never log secrets or raw model output in user-facing errors.** `error.message` is shown to users.
- **Dependencies:** add new ones to `requirements.in` only, then regenerate with `pip-compile --generate-hashes requirements.in`. Prefer the standard library. Every new dependency needs a reason in the PR description.
- **Model names live in config, not code.** Don't hardcode an OpenRouter model ID in a stage.

## Definition of done for a PR

- `pytest`, `ruff check`, `ruff format --check`, and `pip-audit` all pass.
- New behavior has tests, including at least one failure case.
- Contract or design changes are reflected in `docs/`.

## Infra expectations (for whoever wires up AWS)

- Handler: `app.handler.lambda_handler`.
- The SQS trigger must enable `ReportBatchItemFailures`; the handler returns partial batch responses.
- Environment variables are the ones listed in the job contract.
