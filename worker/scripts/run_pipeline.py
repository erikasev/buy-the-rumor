#!/usr/bin/env python3
"""Run the pipeline locally on one theme, the same code path Lambda uses.

Uses the S3 cache under collected-data/ and scrapes X for gaps. Secrets come from the
environment or .env.local. By default the brief is printed and uploaded to S3, but no
DynamoDB record is written; pass --with-dynamodb to run the full Lambda flow.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure worker/ directory is in sys.path
worker_dir = Path(__file__).resolve().parent.parent
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from app.contract import JobFailure, JobMessage, new_ulid  # noqa: E402
from app.jobs import JobStore  # noqa: E402
from app.llm import LLMAgent  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.storage import S3DataStorage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Run Buy the Rumor pipeline on a theme")
    parser.add_argument(
        "--theme",
        type=str,
        default="Bitcoin and crypto liquidity breakout, 2-3 day swing, medium risk",
        help="User prompt/theme",
    )
    parser.add_argument("--risk-level", type=int, default=50, help="0 (cautious) to 100 (bold)")
    parser.add_argument("--no-s3-upload", action="store_true", help="Print the brief without uploading it")
    parser.add_argument(
        "--with-dynamodb", action="store_true", help="Also write the job record, exactly as Lambda does"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    now = datetime.now(timezone.utc)
    msg = JobMessage(
        schema_version=1, job_id=new_ulid(now), theme=args.theme, risk_level=args.risk_level, requested_at=now
    )
    print(f"Job {msg.job_id}\nTheme: {msg.theme}\n")

    pipeline = Pipeline(storage=S3DataStorage(), llm=LLMAgent())

    if args.with_dynamodb:
        pipeline.store = JobStore()
        pipeline.run(msg)
        print(f"\nRecord: {pipeline.store.get(msg.job_id)}")
        return

    try:
        brief = pipeline.generate(msg, enter=lambda stage: print(f"-> {stage}"))
    except JobFailure as f:
        print(f"\n[FAILED] {f.error.stage} / {f.error.code}: {f.error.message}", file=sys.stderr)
        sys.exit(1)

    print(
        "\n"
        + "=" * 65
        + f"\nVALIDATED BRIEF ({len(brief.evidence['sources'])} sources, internal)\n"
        + "=" * 65
    )
    print(brief.markdown)

    if not args.no_s3_upload:
        key = pipeline.save_brief(msg.job_id, msg.requested_at, brief)
        print(f"\n[SAVED] s3://{pipeline.storage.bucket_name}/{key}")


if __name__ == "__main__":
    main()
