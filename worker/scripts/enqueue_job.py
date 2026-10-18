#!/usr/bin/env python3
"""Send one job message to the SQS queue, to test the deployed Lambda without the web service.

Queue URL: JOBS_QUEUE_URL, else `sqs` in settings.yaml. The worker creates the DynamoDB
record itself when it is missing, then runs the job. Prints the job_id to look up.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure worker/ directory is in sys.path
worker_dir = Path(__file__).resolve().parent.parent
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

import boto3  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.contract import JobMessage, new_ulid, utc_iso  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Enqueue a brief job")
    parser.add_argument("--theme", required=True, help="User prompt/theme (1-1000 characters)")
    parser.add_argument("--risk-level", type=int, default=50, help="0 (cautious) to 100 (bold)")
    args = parser.parse_args()

    queue_url = os.getenv("JOBS_QUEUE_URL") or load_settings().get("sqs")
    if not queue_url:
        sys.exit("No queue: set JOBS_QUEUE_URL or `sqs` in settings.yaml")
    region = queue_url.split("//", 1)[1].split(".")[1]  # https://sqs.<region>.amazonaws.com/...

    now = datetime.now(timezone.utc)
    body = {
        "schema_version": 1,
        "job_id": new_ulid(now),
        "theme": args.theme.strip(),
        "risk_level": args.risk_level,
        "requested_at": utc_iso(now),
    }
    JobMessage.model_validate(body)  # fail here, not in the worker
    boto3.client("sqs", region_name=region).send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))
    print(body["job_id"])


if __name__ == "__main__":
    main()
