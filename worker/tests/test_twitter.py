import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.twitter import TwitterAPIError, TwitterCollector, collect_and_save_tweets, get_twitter_bearer_token


def test_get_twitter_bearer_token_from_env(monkeypatch):
    monkeypatch.setenv("TWITTER_BEARER_TOKEN", "test-env-token")
    assert get_twitter_bearer_token() == "test-env-token"


def test_twitter_collector_requires_token(monkeypatch):
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    with patch("app.config.find_env_file", return_value=None):
        with pytest.raises(ValueError, match="TWITTER_BEARER_TOKEN not found"):
            TwitterCollector(bearer_token=None)


def test_get_users_by_usernames():
    mock_resp_data = {
        "data": [
            {"id": "101", "name": "Lookonchain", "username": "lookonchain"},
            {"id": "102", "name": "FedGuy", "username": "FedGuy12"},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        collector = TwitterCollector(bearer_token="dummy-token")
        users = collector.get_users_by_usernames(["@lookonchain", "FedGuy12"])
        assert "lookonchain" in users
        assert users["lookonchain"]["id"] == "101"
        assert "fedguy12" in users
        assert users["fedguy12"]["id"] == "102"


def test_get_user_tweets():
    mock_resp_data = {
        "data": [
            {
                "id": "tweet1",
                "text": "Market looks bullish on AI compute",
                "created_at": "2026-10-17T12:00:00.000Z",
                "public_metrics": {"like_count": 50, "retweet_count": 10},
            }
        ],
        "meta": {"result_count": 1},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        collector = TwitterCollector(bearer_token="dummy-token")
        tweets = collector.get_user_tweets(
            user_id="101",
            days=3,
            now=datetime(2026, 10, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert len(tweets) == 1
        assert tweets[0]["id"] == "tweet1"


def test_get_user_tweets_handles_rate_limit():
    collector = TwitterCollector(bearer_token="dummy-token")
    with patch.object(collector, "_request", side_effect=TwitterAPIError(429, "Rate limit exceeded")):
        tweets = collector.get_user_tweets(user_id="101", days=3)
        assert tweets == []


def test_collect_tweets_formats_sources():
    collector = TwitterCollector(bearer_token="dummy-token")
    mock_user_map = {"lookonchain": {"id": "101", "name": "Lookonchain", "username": "lookonchain"}}
    mock_tweets = [
        {
            "id": "t1",
            "text": "Whale bought 1000 ETH",
            "created_at": "2026-10-17T10:00:00.000Z",
            "public_metrics": {"like_count": 25},
        }
    ]

    with patch.object(collector, "get_users_by_usernames", return_value=mock_user_map):
        with patch.object(collector, "get_user_tweets", return_value=mock_tweets):
            result = collector.collect_tweets(accounts=["@lookonchain"], days=3)
            assert result["total_tweets"] == 1
            tweet = result["tweets"][0]
            assert tweet["source_id"] == "X1"
            assert tweet["author_username"] == "lookonchain"
            assert "https://x.com/lookonchain/status/t1" in tweet["url"]


def test_collect_and_save_tweets_calls_s3():
    mock_storage = MagicMock()
    mock_storage.save_batch.return_value = "collected-data/2026-10-18T00-00-00Z/tweets.json"

    with patch("app.twitter.TwitterCollector") as mock_col_cls:
        mock_instance = MagicMock()
        mock_col_cls.return_value = mock_instance
        mock_instance.collect_tweets.return_value = {
            "total_tweets": 5,
            "tweets": [{"id": "1"}],
        }

        data, s3_key = collect_and_save_tweets(storage=mock_storage, days=3)
        assert data["total_tweets"] == 5
        assert s3_key == "collected-data/2026-10-18T00-00-00Z/tweets.json"
        mock_storage.save_batch.assert_called_once()
