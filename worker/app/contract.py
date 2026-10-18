"""Types for the job contract (docs/contracts/job-v1.md).

Only what crosses the web / worker boundary lives here. If this file and the
contract document disagree, the document wins and this file is the bug.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Crockford base32, 26 characters, first character 0-7.
ULID_PATTERN = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]

Stage = Literal[
    "universe",
    "theme_mapping",
    "collecting",
    "sentiment",
    "strategy",
    "review",
    "validation",
]

ErrorCode = Literal[
    "NO_DATA",
    "LLM_INVALID_OUTPUT",
    "LLM_UNAVAILABLE",
    "REVIEW_REJECTED",
    "LIMIT_EXCEEDED",
    "INTERNAL",
]

# Legal status transitions. Anything not listed must be refused.
TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"RUNNING", "FAILED"},
    "RUNNING": {"SUCCEEDED", "FAILED"},
    "SUCCEEDED": set(),
    "FAILED": set(),
}


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


class JobMessage(BaseModel):
    """The SQS message the web service sends and the worker consumes."""

    # Readers ignore fields they don't know, so the contract can grow additively.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    schema_version: Literal[1]
    job_id: str = Field(pattern=ULID_PATTERN)
    theme: str = Field(min_length=1, max_length=100)
    requested_at: AwareDatetime


class JobError(BaseModel):
    """Shape of the `error` map on a FAILED record. `message` is shown to users."""

    stage: Stage
    code: ErrorCode
    message: str = Field(min_length=1, max_length=300)
