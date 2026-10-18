# ADR-0001: Data sources stay internal

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-10-17 |
| Deciders | Erika, Daniel |

## Context

The design originally had every brief cite its sources, listed at the end with URLs. Which data sources we use, and how we combine them, is part of what makes Buy the Rumor useful, so we don't want to publish it.

Citations also do safety work: they let the risk reviewer check that every claim is backed by real evidence, which guards against made-up claims and prompt injection.

## Decision

Sources are used internally and never shown to users.

- **Inside the pipeline, nothing changes.** The collector labels each piece of evidence with a source ID, the agents tie every claim to IDs, and the risk reviewer checks them. The raw evidence is saved privately as `<job_id>.sources.json` in S3.
- **Everything users receive has no sources:** the web page, the `.md` download, and the `<job_id>.json` brief view. The renderer strips citation markers after the brief passes validation.
- `<job_id>.sources.json` is never served by the web service. Only the worker's IAM role can write it, and the web service's role has no read access to it.

## Consequences

- We keep the safety benefit of citations without exposing our data sources.
- Users can't check a claim against its source themselves, so the "decision support, not advice" framing matters more.
- Evals and debugging still have full traceability through the private evidence files.
- Changes the brief format in `docs/design.md` and the S3 layout in `docs/contracts/job-v1.md`.
