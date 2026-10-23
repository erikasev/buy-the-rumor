import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.collector import (
    CollectorConfig,
    PostCollector,
    SourceAPIError,
    collect_and_save_posts,
    get_sources_token,
)

# tests/fixtures/collector.test.json is a made-up API: the real one is described only in the
# private config/collector.json.
TEST_CONFIG = json.loads((Path(__file__).parent / "fixtures" / "collector.test.json").read_text())
CONFIG = CollectorConfig.model_validate(TEST_CONFIG)


def api_returning(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    return patch("urllib.request.urlopen", return_value=resp)


def requested_url(urlopen):
    return urlparse(urlopen.call_args.args[0].full_url)


def test_token_comes_from_sources_api_token(monkeypatch):
    monkeypatch.setenv("SOURCES_API_TOKEN", "test-env-token")
    assert get_sources_token() == "test-env-token"


def test_collector_requires_token(monkeypatch):
    monkeypatch.delenv("SOURCES_API_TOKEN", raising=False)
    with patch("app.config.find_env_file", return_value=None):
        with pytest.raises(ValueError, match="SOURCES_API_TOKEN not found"):
            PostCollector(CONFIG, token=None)


def test_config_rejects_unknown_fields_and_plain_http():
    with pytest.raises(ValueError):
        CollectorConfig.model_validate({**TEST_CONFIG, "surprise": 1})
    with pytest.raises(ValueError):
        CollectorConfig.model_validate({**TEST_CONFIG, "base_url": "http://insecure.example"})
    assert CONFIG.post_host == "posts.example"


def test_lookup_uses_configured_path_and_params():
    payload = {
        "data": [
            {"id": "101", "name": "Whale Watch", "username": "whale_watch"},
            {"id": "102", "name": "Repo Desk", "username": "repo_desk"},
        ]
    }
    with api_returning(payload) as urlopen:
        accounts = PostCollector(CONFIG, token="t").lookup_accounts(["@whale_watch", "repo_desk"])
    url = requested_url(urlopen)
    assert url.path == "/v1/accounts/lookup"
    assert parse_qs(url.query) == {"handles": ["whale_watch,repo_desk"], "fields": ["id,name,username"]}
    assert accounts["whale_watch"]["id"] == "101" and accounts["repo_desk"]["id"] == "102"


def test_account_posts_expand_quotes_and_reposts_by_configured_field_names():
    payload = {
        "data": [
            {
                "id": "p1",
                "text": "Look at this",
                "created_at": "2026-10-17T12:00:00Z",
                "stats": {"likes": 5},
                "refs": [{"type": "quote", "id": "q1"}],
            },
            {
                "id": "p2",
                "text": "RP",
                "created_at": "2026-10-17T11:00:00Z",
                "refs": [{"type": "repost", "id": "q2"}],
            },
        ],
        "includes": {
            "posts": [
                {"id": "q1", "author_id": "9", "text": "Spreads widened"},
                {"id": "q2", "author_id": "9", "text": "Tankers idle"},
            ],
            "accounts": [{"id": "9", "username": "credit_notes"}],
        },
    }
    with api_returning(payload) as urlopen:
        posts = PostCollector(CONFIG, token="t").get_account_posts(
            "101", days=3, max_results=2, now=datetime(2026, 10, 18, tzinfo=timezone.utc)
        )
    url = requested_url(urlopen)
    assert url.path == "/v1/accounts/101/posts"
    q = parse_qs(url.query)
    assert q["since"] == ["2026-10-15T00:00:00Z"] and q["limit"] == ["5"]  # clamped up to min_limit
    assert posts[0]["text"] == "Look at this\n[Quoting @credit_notes: Spreads widened]"
    assert posts[0]["metrics"] == {"likes": 5}
    assert posts[1]["text"] == "[Repost of @credit_notes]: Tankers idle"


def test_rate_limit_skips_the_account():
    collector = PostCollector(CONFIG, token="t")
    with patch.object(collector, "_request", side_effect=SourceAPIError(429, "Rate limit exceeded")):
        assert collector.get_account_posts("101", days=3) == []


def test_collect_posts_labels_sources_and_builds_links():
    collector = PostCollector(CONFIG, token="t")
    resolved = {"whale_watch": {"id": "101", "name": "Whale Watch", "username": "whale_watch"}}
    posts = [{"id": "t1", "text": "Whale bought 1000 ETH", "created_at": "2026-10-17T10:00:00Z"}]
    with patch.object(collector, "lookup_accounts", return_value=resolved):
        with patch.object(collector, "get_account_posts", return_value=posts):
            result = collector.collect_posts(accounts=["@whale_watch"], days=3)
    assert result["total_posts"] == 1 and result["accounts"] == ["whale_watch"]
    post = result["posts"][0]
    assert post["source_id"] == "S1" and post["post_id"] == "t1"
    assert post["url"] == "https://posts.example/whale_watch/p/t1"


def test_collect_and_save_posts_reads_private_config_and_saves_batch():
    storage = MagicMock()
    files = {
        "config/sources.json": {
            "version": 1,
            "accounts": [{"handle": "@whale_watch", "focus": "On-chain flows"}],
        },
        "config/collector.json": TEST_CONFIG,
    }
    storage.get.side_effect = lambda key: json.dumps(files[key]).encode()
    storage.save_batch.return_value = "collected-data/2026-10-18T00-00-00Z/posts.json"
    with patch("app.collector.PostCollector") as collector_cls:
        collector_cls.return_value.collect_posts.return_value = {"total_posts": 5, "posts": [{"id": "1"}]}
        data, key = collect_and_save_posts(storage=storage, days=3)
    assert collector_cls.call_args.args[0].base_url == "https://api.posts.example/v1"
    assert collector_cls.return_value.collect_posts.call_args.kwargs["accounts"] == ["@whale_watch"]
    assert data["total_posts"] == 5 and key.endswith("/posts.json")
    assert storage.save_batch.call_args.kwargs["filename"] == "posts.json"
