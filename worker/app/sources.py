"""The X accounts the worker follows, and what each one covers.

Which sources we use is private (docs/adr/0001-data-sources-stay-internal.md), so the list
is not in the repo or the image. It lives in the bucket at `config/sources.json`:

    {"version": 1, "accounts": [{"handle": "@someone", "focus": "What they cover"}, ...]}

It is read fresh for every job, so editing the file takes effect without a redeploy.
SOURCES_KEY overrides the S3 key; SOURCES_FILE (a local path) replaces S3 for offline runs.
A missing or invalid list fails the job: the worker never runs without its sources.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import setting
from app.contract import JobFailure

logger = logging.getLogger(__name__)

DEFAULT_KEY = "config/sources.json"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handle: str = Field(pattern=r"^@[A-Za-z0-9_]{1,15}$")
    focus: str = Field(min_length=1, max_length=300)


class SourceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    accounts: list[Source] = Field(min_length=1, max_length=200)


def sources_config_key(settings: dict[str, Any]) -> str:
    return os.getenv("SOURCES_KEY") or setting(settings, "sources_key", DEFAULT_KEY)


def load_sources(storage: Any, settings: dict[str, Any]) -> list[Source]:
    """The followed accounts, in file order. Raises JobFailure if the list can't be loaded."""
    local = os.getenv("SOURCES_FILE")
    where = local or f"s3://{storage.bucket_name}/{sources_config_key(settings)}"
    try:
        raw = Path(local).read_bytes() if local else storage.get(sources_config_key(settings))
        sources = SourceList.model_validate(json.loads(raw)).accounts
    except (OSError, ClientError, ValueError, ValidationError) as e:
        # The reason goes to the logs; users only learn that sources aren't configured.
        logger.error(json.dumps({"event": "sources_unavailable", "where": where, "error": str(e)[:500]}))
        raise JobFailure("theme_mapping", "INTERNAL", "No sources are configured.") from e
    handles = [s.handle.lower() for s in sources]
    if len(set(handles)) != len(handles):
        logger.error(
            json.dumps({"event": "sources_unavailable", "where": where, "error": "duplicate handles"})
        )
        raise JobFailure("theme_mapping", "INTERNAL", "No sources are configured.")
    return sources


def focus_by_handle(sources: list[Source]) -> dict[str, str]:
    """Lowercased handle without '@' -> what the account covers."""
    return {s.handle.lstrip("@").lower(): s.focus for s in sources}
