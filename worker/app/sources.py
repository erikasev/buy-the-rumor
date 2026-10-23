"""The accounts the worker follows, and what each one covers.

Which sources we use is private (docs/adr/0001-data-sources-stay-internal.md), so neither
the list nor the collector's API details are in the repo or the image. They live in the
bucket as private config files, read fresh for every job (edits take effect without a
redeploy):

    config/sources.json    {"version": 1, "accounts": [{"handle": "@someone", "focus": "..."}]}
    config/collector.json  the source API's endpoints and field names (app.collector)

<NAME>_KEY overrides a file's S3 key; <NAME>_FILE (a local path) replaces S3 for offline
runs. A missing or invalid file fails the job: the worker never runs without its sources.
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


def private_key(settings: dict[str, Any], name: str, default: str) -> str:
    """S3 key of a private config file: <NAME>_KEY, else settings `<name>_key`, else `default`."""
    return os.getenv(f"{name.upper()}_KEY") or setting(settings, f"{name}_key", default)


def load_private[M: BaseModel](
    storage: Any, settings: dict[str, Any], name: str, default: str, model: type[M]
) -> M:
    """Load and validate a private config file. Raises JobFailure if it can't be loaded."""
    local = os.getenv(f"{name.upper()}_FILE")
    key = private_key(settings, name, default)
    where = local or f"s3://{storage.bucket_name}/{key}"
    try:
        raw = Path(local).read_bytes() if local else storage.get(key)
        return model.model_validate(json.loads(raw))
    except (OSError, ClientError, ValueError, ValidationError) as e:
        # The reason goes to the logs; users only learn that sources aren't configured.
        logger.error(json.dumps({"event": "config_unavailable", "where": where, "error": str(e)[:500]}))
        raise JobFailure("theme_mapping", "INTERNAL", "No sources are configured.") from e


def load_sources(storage: Any, settings: dict[str, Any]) -> list[Source]:
    """The followed accounts, in file order. Raises JobFailure if the list can't be loaded."""
    sources = load_private(storage, settings, "sources", DEFAULT_KEY, SourceList).accounts
    handles = [s.handle.lower() for s in sources]
    if len(set(handles)) != len(handles):
        logger.error(
            json.dumps({"event": "config_unavailable", "where": "sources", "error": "duplicate handles"})
        )
        raise JobFailure("theme_mapping", "INTERNAL", "No sources are configured.")
    return sources


def focus_by_handle(sources: list[Source]) -> dict[str, str]:
    """Lowercased handle without '@' -> what the account covers."""
    return {s.handle.lstrip("@").lower(): s.focus for s in sources}
