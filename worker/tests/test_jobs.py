import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.contract import JobError, JobMessage
from app.jobs import JobStore, resolve_table

NOW = datetime(2026, 10, 21, 13, 0, 5, tzinfo=timezone.utc)


def condition_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


@pytest.fixture
def msg(fixture_text):
    return JobMessage.model_validate_json(fixture_text("message.valid.json"))


def test_resolve_table_from_arn(monkeypatch):
    monkeypatch.delenv("JOBS_TABLE_NAME", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-west-1")
    arn = "arn:aws:dynamodb:us-east-2:351611731335:table/buy-the-rumor"
    assert resolve_table({"dynamodb": arn}) == ("buy-the-rumor", "us-east-2")


def test_resolve_table_env_wins(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE_NAME", "briefs")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert resolve_table({"dynamodb": "arn:aws:dynamodb:us-east-2:1:table/x"}) == ("briefs", "eu-west-1")


def test_start_is_conditional_on_queued_or_missing(msg, fixture_text):
    table = MagicMock()
    table.update_item.return_value = {
        "Attributes": {"status": "RUNNING", "created_at": "2026-10-21T13:00:00Z"}
    }
    started, record = JobStore(table=table).start(msg, NOW)
    assert started and record["status"] == "RUNNING"
    kw = table.update_item.call_args.kwargs
    assert kw["ConditionExpression"] == "attribute_not_exists(#id) OR #s = :queued"
    assert kw["ExpressionAttributeNames"]["#s"] == "status"
    values = kw["ExpressionAttributeValues"]
    assert values[":created"] == "2026-10-21T13:00:00Z"
    assert values[":ttl"] == json.loads(fixture_text("record.queued.json"))["ttl"]


def test_start_on_duplicate_delivery_returns_current_record(msg):
    table = MagicMock()
    table.update_item.side_effect = condition_failed()
    table.get_item.return_value = {"Item": {"status": "SUCCEEDED"}}
    started, record = JobStore(table=table).start(msg, NOW)
    assert not started and record["status"] == "SUCCEEDED"


def test_start_raises_other_errors(msg):
    table = MagicMock()
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceeded"}}, "UpdateItem"
    )
    with pytest.raises(ClientError):
        JobStore(table=table).start(msg, NOW)


def test_succeed_requires_running():
    table = MagicMock()
    JobStore(table=table).succeed("J", "briefs/2026/10/21/J.md", NOW)
    kw = table.update_item.call_args.kwargs
    assert kw["ConditionExpression"] == "#s = :running"
    assert kw["ExpressionAttributeValues"][":key"] == "briefs/2026/10/21/J.md"


def test_fail_writes_contract_error_and_ignores_finished_jobs():
    table = MagicMock()
    store = JobStore(table=table)
    error = JobError(stage="validation", code="LIMIT_EXCEEDED", message="Too big.")
    store.fail("J", error, NOW)
    kw = table.update_item.call_args.kwargs
    assert kw["ExpressionAttributeValues"][":error"] == {
        "stage": "validation",
        "code": "LIMIT_EXCEEDED",
        "message": "Too big.",
    }
    assert kw["ConditionExpression"] == "#s IN (:queued, :running)"

    table.update_item.side_effect = condition_failed()
    store.fail("J", error, NOW)  # already final: no exception
