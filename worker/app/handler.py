"""Lambda entry point. SQS invokes this with a batch of job messages.

For now it parses each message per the job contract and logs it. The agent
pipeline gets wired in by later PRs.

Returns a partial batch response so one bad message doesn't make SQS retry the
whole batch. The SQS trigger must enable ReportBatchItemFailures.
"""

import json
import logging

from pydantic import ValidationError

from app.contract import JobMessage

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def run_job(msg: JobMessage) -> None:
    """Placeholder for the agent pipeline."""
    logger.info(json.dumps({"event": "job_received", "job_id": msg.job_id, "theme": msg.theme}))


def lambda_handler(event: dict, context: object) -> dict:
    failures: list[dict] = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            msg = JobMessage.model_validate_json(record["body"])
        except (ValidationError, KeyError) as exc:
            # A malformed message will never succeed, so retrying it is pointless.
            # Log it and let SQS delete it.
            logger.error(json.dumps({"event": "bad_message", "message_id": message_id, "error": str(exc)}))
            continue
        try:
            run_job(msg)
        except Exception:
            logger.exception(json.dumps({"event": "job_crashed", "job_id": msg.job_id}))
            failures.append({"itemIdentifier": message_id})  # SQS will redeliver this one
    return {"batchItemFailures": failures}
