import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.contract import JobFailure
from app.sources import focus_by_handle, load_sources, private_key

VALID = {"version": 1, "accounts": [{"handle": "@whale_watch", "focus": "On-chain flows"}]}


def storage_with(body=None, error=None):
    storage = MagicMock(bucket_name="test-bucket")
    if error:
        storage.get.side_effect = error
    else:
        storage.get.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    return storage


def test_loads_accounts_from_the_configured_key():
    storage = storage_with(VALID)
    sources = load_sources(storage, {"sources_key": "config/sources.json"})
    assert [s.handle for s in sources] == ["@whale_watch"]
    storage.get.assert_called_once_with("config/sources.json")
    assert focus_by_handle(sources) == {"whale_watch": "On-chain flows"}


def test_env_overrides_key_and_local_file_replaces_s3(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCES_KEY", "config/other.json")
    assert private_key({"sources_key": "config/sources.json"}, "sources", "d") == "config/other.json"

    local = tmp_path / "sources.json"
    local.write_text(json.dumps(VALID))
    monkeypatch.setenv("SOURCES_FILE", str(local))
    storage = storage_with(VALID)
    assert load_sources(storage, {})[0].handle == "@whale_watch"
    storage.get.assert_not_called()


@pytest.mark.parametrize(
    "body,error",
    [
        (None, ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")),
        (b"not json", None),
        ({"version": 1, "accounts": []}, None),
        ({"version": 2, "accounts": VALID["accounts"]}, None),
        ({"version": 1, "accounts": [{"handle": "no-at-sign", "focus": "x"}]}, None),
        ({"version": 1, "accounts": [{"handle": "@a", "focus": "x"}, {"handle": "@A", "focus": "y"}]}, None),
    ],
)
def test_missing_or_bad_list_fails_closed(body, error):
    with pytest.raises(JobFailure) as exc:
        load_sources(storage_with(body, error), {})
    assert exc.value.error.message == "No sources are configured."
    assert exc.value.error.code == "INTERNAL"
