"""Lambda entry point. SQS invokes this with a batch of job messages.

Each message runs the agent pipeline (app.pipeline), which keeps the DynamoDB record in
step and stores the brief in S3. Job failures (bad data, LLM errors, limit violations)
end the job as FAILED and are not retried. Only infrastructure errors (e.g. DynamoDB
unreachable) are reported back to SQS for redelivery.

Returns a partial batch response so one bad message doesn't make SQS retry the
whole batch. The SQS trigger must enable ReportBatchItemFailures.
"""

import json
import logging

from pydantic import ValidationError

from app.contract import JobMessage
from app.jobs import JobStore
from app.llm import LLMAgent
from app.pipeline import Pipeline
from app.storage import S3DataStorage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    """Built once per Lambda container, so clients and settings are reused across jobs."""
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline(storage=S3DataStorage(), llm=LLMAgent(), store=JobStore())
    return _pipeline


def run_job(msg: JobMessage, receive_count: int = 1) -> None:
    logger.info(json.dumps({"event": "job_received", "job_id": msg.job_id, "receive_count": receive_count}))
    get_pipeline().run(msg, receive_count)


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
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            run_job(msg, receive_count)
        except Exception:
            logger.exception(json.dumps({"event": "job_crashed", "job_id": msg.job_id}))
            failures.append({"itemIdentifier": message_id})  # SQS will redeliver this one
    return {"batchItemFailures": failures}
