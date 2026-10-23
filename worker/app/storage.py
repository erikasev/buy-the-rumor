"""S3 storage and data collection retrieval.

Handles reading and writing collected data to/from S3 with the prefix:
`collected-data/{timestamp}/`
as specified in AGENTS.md and settings.yaml.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from dateutil import parser as date_parser

from app.config import load_settings

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-2"
DEFAULT_PREFIX = "collected-data/"


class S3DataStorage:
    """Manages reading and writing data batches in S3."""

    def __init__(
        self,
        bucket_name: str | None = None,
        region_name: str | None = None,
        s3_client: Any | None = None,
    ):
        settings = load_settings()
        self.bucket_name = (
            bucket_name
            or os.getenv("BRIEFS_BUCKET_NAME")
            or os.getenv("COLLECTED_DATA_BUCKET_NAME")
            or settings.get("bucket_name")
        )
        if not self.bucket_name:
            raise ValueError(
                "S3 bucket_name not specified. Set bucket_name in settings.yaml, "
                "or export BRIEFS_BUCKET_NAME or pass bucket_name to S3DataStorage."
            )

        self.region_name = region_name or os.getenv("AWS_REGION") or settings.get("region") or DEFAULT_REGION
        self.s3_client = s3_client or boto3.client("s3", region_name=self.region_name)

    def list_batches(self, prefix: str = DEFAULT_PREFIX) -> list[dict[str, Any]]:
        """List all batch prefixes under `collected-data/` sorted descending by timestamp.

        Returns a list of dicts:
        [
            {
                "prefix": "collected-data/2026-10-17T20:00:00Z/",
                "timestamp_str": "2026-10-17T20:00:00Z",
                "timestamp": datetime(...),
                "last_modified": datetime(...)
            },
            ...
        ]
        """
        norm_prefix = prefix.strip("/") + "/"
        batches: list[dict[str, Any]] = []

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=norm_prefix,
                Delimiter="/",
            )

            for page in pages:
                for common_prefix in page.get("CommonPrefixes", []):
                    sub_prefix = common_prefix.get("Prefix", "")
                    # Extract timestamp folder name:
                    # e.g. "collected-data/2026-10-17T20:00:00Z/" -> "2026-10-17T20:00:00Z"
                    folder_name = sub_prefix[len(norm_prefix) :].strip("/")
                    if not folder_name:
                        continue

                    # Try parsing the folder name as a datetime
                    parsed_dt: datetime | None = None
                    try:
                        clean_time = folder_name
                        if "T" in clean_time:
                            d_part, t_part = clean_time.split("T", 1)
                            clean_time = f"{d_part}T{t_part.replace('-', ':').replace('_', ':')}"
                        parsed_dt = date_parser.parse(clean_time)
                        if parsed_dt.tzinfo is None:
                            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            if folder_name.isdigit():
                                parsed_dt = datetime.fromtimestamp(float(folder_name), tz=timezone.utc)
                        except Exception:
                            pass

                    batches.append(
                        {
                            "prefix": sub_prefix,
                            "timestamp_str": folder_name,
                            "timestamp": parsed_dt,
                        }
                    )

            # Sort batches descending: newest first
            batches.sort(
                key=lambda b: (
                    b["timestamp"] if b["timestamp"] else datetime.min.replace(tzinfo=timezone.utc),
                    b["timestamp_str"],
                ),
                reverse=True,
            )

        except ClientError as e:
            logger.error("Failed to list batches in bucket %s: %s", self.bucket_name, e)
            raise

        return batches

    def get_latest_batch(
        self,
        prefix: str = DEFAULT_PREFIX,
        max_age_hours: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Get the latest batch if available and within max_age_hours (if specified).

        Returns None if no batches exist or if the latest batch is older than max_age_hours.
        """
        batches = self.list_batches(prefix=prefix)
        if not batches:
            return None

        latest = batches[0]
        if max_age_hours is not None and latest.get("timestamp"):
            current_time = now or datetime.now(timezone.utc)
            batch_time = latest["timestamp"]
            age = current_time - batch_time
            if age > timedelta(hours=max_age_hours):
                logger.info(
                    "Latest batch %s is older (%s) than max_age_hours (%.1f)",
                    latest["prefix"],
                    age,
                    max_age_hours,
                )
                return None

        return latest

    def read_batch_files(self, batch_prefix: str) -> list[dict[str, Any]]:
        """Read and parse all JSON/text objects within a batch prefix.

        Returns a list of dicts:
        [
            {
                "key": "collected-data/.../tweets.json",
                "filename": "tweets.json",
                "data": parsed_content,
                "last_modified": datetime(...)
            }
        ]
        """
        results: list[dict[str, Any]] = []
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=batch_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith("/"):
                        continue  # Skip directory markers
                    filename = Path(key).name
                    resp = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
                    raw_bytes = resp["Body"].read()

                    # Attempt JSON parsing, fall back to decoded string
                    content: Any = None
                    try:
                        content = json.loads(raw_bytes.decode("utf-8"))
                    except Exception:
                        content = raw_bytes.decode("utf-8", errors="replace")

                    results.append(
                        {
                            "key": key,
                            "filename": filename,
                            "data": content,
                            "last_modified": obj.get("LastModified"),
                        }
                    )
        except ClientError as e:
            logger.error("Failed to read objects in batch %s: %s", batch_prefix, e)
            raise

        return results

    def save_batch(
        self,
        data: Any,
        timestamp: str | None = None,
        filename: str = "tweets.json",
        prefix: str = DEFAULT_PREFIX,
    ) -> str:
        """Upload newly collected data to s3://{bucket}/{prefix}/{timestamp}/{filename}.

        Returns the full S3 key written.
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

        norm_prefix = prefix.strip("/") + "/"
        key = f"{norm_prefix}{timestamp}/{filename}"

        if isinstance(data, (dict, list)):
            body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        elif isinstance(data, str):
            body = data.encode("utf-8")
            content_type = "text/plain"
        elif isinstance(data, bytes):
            body = data
            content_type = "application/octet-stream"
        else:
            body = json.dumps(data, default=str).encode("utf-8")
            content_type = "application/json"

        return self.put(key, body, content_type)

    def get(self, key: str) -> bytes:
        """Read one object. Raises botocore's ClientError if it is missing or unreadable."""
        return self.s3_client.get_object(Bucket=self.bucket_name, Key=key)["Body"].read()

    def put(self, key: str, body: bytes, content_type: str) -> str:
        """Write one object and return its key."""
        try:
            self.s3_client.put_object(Bucket=self.bucket_name, Key=key, Body=body, ContentType=content_type)
        except ClientError as e:
            logger.error("Failed to write s3://%s/%s: %s", self.bucket_name, key, e)
            raise
        logger.info("Wrote s3://%s/%s", self.bucket_name, key)
        return key


def check_collected_data_status(
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    """High-level inspection of the S3 collected-data prefix.

    Returns summary dictionary with counts, latest batch, and whether fresh data is present.
    """
    storage = S3DataStorage()
    batches = storage.list_batches()
    latest = storage.get_latest_batch(max_age_hours=max_age_hours)

    return {
        "bucket_name": storage.bucket_name,
        "region": storage.region_name,
        "total_batches": len(batches),
        "latest_batch": batches[0] if batches else None,
        "has_fresh_data": latest is not None,
        "fresh_batch": latest,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Checking S3 collected data status...")
    try:
        status = check_collected_data_status()
        print(f"Bucket: {status['bucket_name']}")
        print(f"Total batches found: {status['total_batches']}")
        if status["latest_batch"]:
            lb = status["latest_batch"]
            print(f"Latest batch: {lb['prefix']} ({lb['timestamp_str']})")
            print(f"Fresh (within 24h)? {status['has_fresh_data']}")
        else:
            print("No batches found under collected-data/ yet.")
    except Exception as exc:
        print(f"Error checking S3: {exc}")
