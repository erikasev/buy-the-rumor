import json

import pytest
from pydantic import ValidationError

from app.contract import JobError, JobMessage, can_transition
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
        ("theme", "x" * 101),
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


def _sqs_event(*bodies):
    return {"Records": [{"messageId": f"m{i}", "body": b} for i, b in enumerate(bodies)]}


def test_handler_accepts_valid_message(fixture_text):
    result = lambda_handler(_sqs_event(fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": []}


def test_handler_drops_malformed_message_without_retry(fixture_text):
    result = lambda_handler(_sqs_event("{not json", fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": []}


def test_handler_reports_crashed_job_for_retry(fixture_text, monkeypatch):
    def boom(msg):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr("app.handler.run_job", boom)
    result = lambda_handler(_sqs_event(fixture_text("message.valid.json")), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
