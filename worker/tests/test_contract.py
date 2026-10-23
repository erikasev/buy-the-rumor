import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.contract import (
    ULID_PATTERN,
    JobError,
    JobFailure,
    JobMessage,
    brief_key,
    can_transition,
    new_ulid,
    sources_key,
)
from app.handler import lambda_handler


def test_valid_message_fixture_parses(fixture_text):
    msg = JobMessage.model_validate_json(fixture_text("message.valid.json"))
    assert msg.theme == "AI and compute"


def test_message_missing_theme_is_rejected(fixture_text):
    with pytest.raises(ValidationError):
        JobMessage.model_validate_json(fixture_text("message.missing_theme.json"))


def test_missing_risk_level_defaults_to_50(fixture_text):
    data = json.loads(fixture_text("message.valid.json"))
    del data["risk_level"]
    assert JobMessage.model_validate(data).risk_level == 50


def test_unknown_message_fields_are_ignored(fixture_text):
    data = json.loads(fixture_text("message.valid.json"))
    data["added_in_a_later_version"] = True
    JobMessage.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("job_id", "not-a-ulid"),
        ("theme", "   "),
        ("theme", "x" * 1001),
        ("requested_at", "2026-10-21T13:00:00"),  # no timezone
        ("risk_level", -1),
        ("risk_level", 101),
    ],
)
def test_bad_message_values_are_rejected(fixture_text, field, value):
    data = json.loads(fixture_text("message.valid.json"))
    data[field] = value
    with pytest.raises(ValidationError):
        JobMessage.model_validate(data)


@pytest.mark.parametrize("name", ["record.queued.json", "record.succeeded.json", "record.failed.json"])
def test_record_fixtures_use_contract_values(fixture_text, name):
    record = json.loads(fixture_text(name))
    assert record["status"] in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED"}
    if record["status"] == "SUCCEEDED":
        assert record["s3_key"].endswith(f"{record['job_id']}.md")
    if record["status"] == "FAILED":
        JobError.model_validate(record["error"])


@pytest.mark.parametrize(
    "current,new,allowed",
    [
        ("QUEUED", "RUNNING", True),
        ("QUEUED", "FAILED", True),
        ("RUNNING", "SUCCEEDED", True),
        ("RUNNING", "FAILED", True),
        ("SUCCEEDED", "RUNNING", False),
        ("FAILED", "QUEUED", False),
        ("QUEUED", "SUCCEEDED", False),
    ],
)
def test_status_transitions(current, new, allowed):
    assert can_transition(current, new) is allowed


def test_new_ulid_matches_contract_pattern():
    import re

    ids = {new_ulid() for _ in range(100)}
    assert len(ids) == 100
    assert all(re.match(ULID_PATTERN, i) for i in ids)
    early, late = (
        new_ulid(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        new_ulid(datetime(2026, 6, 1, tzinfo=timezone.utc)),
    )
    assert early < late  # sorts by time


def test_s3_keys_follow_contract(fixture_text):
    record = json.loads(fixture_text("record.succeeded.json"))
    created = datetime.fromisoformat(record["created_at"])
    assert brief_key(record["job_id"], created) == record["s3_key"]
    assert sources_key(record["job_id"], created) == record["s3_key"].replace(".md", ".sources.json")


def test_job_failure_message_fits_contract():
    f = JobFailure("validation", "LIMIT_EXCEEDED", "x" * 500)
    assert len(f.error.message) == 300


def _sqs_event(*bodies):
    return {
        "Records": [
            {"messageId": f"m{i}", "body": b, "attributes": {"ApproximateReceiveCount": "2"}}
            for i, b in enumerate(bodies)
        ]
    }


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.handler.run_job", lambda msg, receive_count: calls.append((msg.job_id, receive_count))
    )
    return calls


def test_handler_runs_valid_message(fixture_text, ran):
    result = lambda_handler(_sqs_event(fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": []}
    assert ran == [("01JADQ7Y3K8V5C2N9W4R6T1M0P", 2)]


def test_handler_drops_malformed_message_without_retry(fixture_text, ran):
    result = lambda_handler(_sqs_event("{not json", fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": []}
    assert len(ran) == 1


def test_handler_reports_crashed_job_for_retry(fixture_text, monkeypatch):
    def boom(msg, receive_count):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr("app.handler.run_job", boom)
    result = lambda_handler(_sqs_event(fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
