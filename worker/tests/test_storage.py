from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.storage import S3DataStorage, load_settings


def test_load_settings_finds_bucket():
    settings = load_settings()
    assert settings.get("bucket_name") == "hackathon-btr-351611731335-us-east-2-an"
    assert settings["sources_key"] == "config/sources.json"


def test_followed_accounts_are_not_in_the_repo():
    # ADR-0001: the account list lives in the bucket (config/sources.json), never in settings.yaml.
    settings = load_settings()
    assert "@" not in str(settings)


def test_storage_init_from_settings():
    with patch("boto3.client"):
        storage = S3DataStorage()
        assert storage.bucket_name == "hackathon-btr-351611731335-us-east-2-an"
        assert storage.region_name == "us-east-2"


def test_storage_init_raises_without_bucket(monkeypatch):
    monkeypatch.delenv("BRIEFS_BUCKET_NAME", raising=False)
    monkeypatch.delenv("COLLECTED_DATA_BUCKET_NAME", raising=False)
    with patch("app.storage.load_settings", return_value={}):
        with pytest.raises(ValueError, match="S3 bucket_name not specified"):
            S3DataStorage()


def test_list_batches_sorts_descending():
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_s3.get_paginator.return_value = mock_paginator

    mock_paginator.paginate.return_value = [
        {
            "CommonPrefixes": [
                {"Prefix": "collected-data/2026-10-15T12-00-00Z/"},
                {"Prefix": "collected-data/2026-10-17T18-00-00Z/"},
                {"Prefix": "collected-data/2026-10-16T09-00-00Z/"},
            ]
        }
    ]

    storage = S3DataStorage(bucket_name="test-bucket", s3_client=mock_s3)
    batches = storage.list_batches()

    assert len(batches) == 3
    # Check that the newest batch (Oct 17) is first
    assert batches[0]["timestamp_str"] == "2026-10-17T18-00-00Z"
    assert batches[1]["timestamp_str"] == "2026-10-16T09-00-00Z"
    assert batches[2]["timestamp_str"] == "2026-10-15T12-00-00Z"


def test_get_latest_batch_freshness():
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_s3.get_paginator.return_value = mock_paginator

    # Batch from 2 hours ago
    mock_paginator.paginate.return_value = [
        {
            "CommonPrefixes": [
                {"Prefix": "collected-data/2026-10-17T12-00-00Z/"},
            ]
        }
    ]

    storage = S3DataStorage(bucket_name="test-bucket", s3_client=mock_s3)

    now_fresh = datetime(2026, 10, 17, 14, 0, 0, tzinfo=timezone.utc)  # 2 hours later
    fresh_batch = storage.get_latest_batch(max_age_hours=6, now=now_fresh)
    assert fresh_batch is not None
    assert fresh_batch["timestamp_str"] == "2026-10-17T12-00-00Z"

    now_stale = datetime(2026, 10, 17, 20, 0, 0, tzinfo=timezone.utc)  # 8 hours later
    stale_batch = storage.get_latest_batch(max_age_hours=6, now=now_stale)
    assert stale_batch is None


def test_read_batch_files_parses_json():
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_s3.get_paginator.return_value = mock_paginator

    mock_paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "collected-data/2026-10-17T12-00-00Z/posts.json",
                    "LastModified": datetime.now(timezone.utc),
                },
            ]
        }
    ]

    mock_body = MagicMock()
    mock_body.read.return_value = b'{"posts": [{"id": "1", "text": "Fed keeps rates unchanged"}]}'
    mock_s3.get_object.return_value = {"Body": mock_body}

    storage = S3DataStorage(bucket_name="test-bucket", s3_client=mock_s3)
    files = storage.read_batch_files("collected-data/2026-10-17T12-00-00Z/")

    assert len(files) == 1
    assert files[0]["filename"] == "posts.json"
    assert files[0]["data"]["posts"][0]["id"] == "1"


def test_save_batch_writes_json():
    mock_s3 = MagicMock()
    storage = S3DataStorage(bucket_name="test-bucket", s3_client=mock_s3)

    key = storage.save_batch(
        data={"items": [1, 2, 3]},
        timestamp="2026-10-17T22-00-00Z",
        filename="feed.json",
    )

    assert key == "collected-data/2026-10-17T22-00-00Z/feed.json"
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == key
    assert call_kwargs["ContentType"] == "application/json"
    assert b'"items": [' in call_kwargs["Body"]
