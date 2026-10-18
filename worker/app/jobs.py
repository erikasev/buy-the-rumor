"""The job record in DynamoDB (docs/contracts/job-v1.md, "DynamoDB record").

Every status change is a conditional write that checks the current status, so a
transition outside the contract's state machine fails instead of overwriting.
All attribute names go through ExpressionAttributeNames (`status` and `ttl` are
DynamoDB reserved words).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import load_settings
from app.contract import JobError, JobMessage, Stage, utc_iso

logger = logging.getLogger(__name__)

TTL = timedelta(days=30)
DEFAULT_REGION = "us-east-2"


def resolve_table(settings: dict[str, Any]) -> tuple[str, str]:
    """Return (table_name, region). JOBS_TABLE_NAME wins; else settings.yaml `dynamodb` (name or ARN)."""
    region = os.getenv("AWS_REGION") or settings.get("region") or DEFAULT_REGION
    name = os.getenv("JOBS_TABLE_NAME") or settings.get("dynamodb")
    if not name:
        raise ValueError("No DynamoDB table: set JOBS_TABLE_NAME or `dynamodb` in settings.yaml.")
    if name.startswith("arn:"):
        # arn:aws:dynamodb:<region>:<account>:table/<name>
        parts = name.split(":")
        region, name = parts[3], parts[5].removeprefix("table/")
    return name, region


def _is_condition_failure(e: ClientError) -> bool:
    return e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class JobStore:
    def __init__(self, table: Any | None = None, settings: dict[str, Any] | None = None):
        if table is None:
            name, region = resolve_table(load_settings() if settings is None else settings)
            table = boto3.resource("dynamodb", region_name=region).Table(name)
        self.table = table

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")

    def start(self, msg: JobMessage, now: datetime) -> tuple[bool, dict[str, Any]]:
        """QUEUED -> RUNNING. Returns (started, record).

        If the record is missing (the job was enqueued without the web service, e.g. by
        scripts/enqueue_job.py), the worker creates it from the message. If the job is in any
        other status, nothing changes and `started` is False.
        """
        created = msg.requested_at
        try:
            resp = self.table.update_item(
                Key={"job_id": msg.job_id},
                UpdateExpression=(
                    "SET #s = :running, #u = :now, "
                    "#v = if_not_exists(#v, :v), #t = if_not_exists(#t, :theme), "
                    "#c = if_not_exists(#c, :created), #ttl = if_not_exists(#ttl, :ttl)"
                ),
                ConditionExpression="attribute_not_exists(#id) OR #s = :queued",
                ExpressionAttributeNames={
                    "#id": "job_id",
                    "#s": "status",
                    "#u": "updated_at",
                    "#v": "schema_version",
                    "#t": "theme",
                    "#c": "created_at",
                    "#ttl": "ttl",
                },
                ExpressionAttributeValues={
                    ":running": "RUNNING",
                    ":queued": "QUEUED",
                    ":now": utc_iso(now),
                    ":v": 1,
                    ":theme": msg.theme,
                    ":created": utc_iso(created),
                    ":ttl": int((created + TTL).timestamp()),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            return False, self.get(msg.job_id) or {}
        return True, resp["Attributes"]

    def set_stage(self, job_id: str, stage: Stage, now: datetime) -> None:
        self.table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :stage, #u = :now",
            ConditionExpression="#s = :running",
            ExpressionAttributeNames={"#st": "stage", "#u": "updated_at", "#s": "status"},
            ExpressionAttributeValues={":stage": stage, ":now": utc_iso(now), ":running": "RUNNING"},
        )

    def succeed(self, job_id: str, s3_key: str, now: datetime) -> None:
        """RUNNING -> SUCCEEDED."""
        self.table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :done, #k = :key, #u = :now",
            ConditionExpression="#s = :running",
            ExpressionAttributeNames={"#s": "status", "#k": "s3_key", "#u": "updated_at"},
            ExpressionAttributeValues={
                ":done": "SUCCEEDED",
                ":key": s3_key,
                ":now": utc_iso(now),
                ":running": "RUNNING",
            },
        )

    def fail(self, job_id: str, error: JobError, now: datetime) -> None:
        """QUEUED or RUNNING -> FAILED. A job that already finished is left as it is."""
        try:
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :failed, #e = :error, #st = :stage, #u = :now",
                ConditionExpression="#s IN (:queued, :running)",
                ExpressionAttributeNames={"#s": "status", "#e": "error", "#st": "stage", "#u": "updated_at"},
                ExpressionAttributeValues={
                    ":failed": "FAILED",
                    ":error": error.model_dump(),
                    ":stage": error.stage,
                    ":now": utc_iso(now),
                    ":queued": "QUEUED",
                    ":running": "RUNNING",
                },
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            logger.warning(json.dumps({"event": "fail_skipped_already_final", "job_id": job_id}))
