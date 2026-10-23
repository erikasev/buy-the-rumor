"""X.com (Twitter) data collector using X API v2.

Collects tweets from accounts listed in settings.yaml for the last 3 days
per AGENTS.md requirements.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_secret, load_settings
from app.sources import load_sources
from app.storage import S3DataStorage

logger = logging.getLogger(__name__)

X_API_BASE = "https://api.x.com/2"


def get_twitter_bearer_token() -> str | None:
    """TWITTER_BEARER_TOKEN from the environment (Lambda), or .env.local when run locally."""
    return get_secret("TWITTER_BEARER_TOKEN")


class TwitterAPIError(RuntimeError):
    """Raised when an X API call fails."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"X API error (HTTP {status_code}): {message}")
        self.status_code = status_code
        self.message = message


class TwitterCollector:
    """Collects recent tweets from specified accounts using X API v2."""

    def __init__(self, bearer_token: str | None = None):
        self.bearer_token = bearer_token or get_twitter_bearer_token()
        if not self.bearer_token:
            raise ValueError(
                "TWITTER_BEARER_TOKEN not found. Set it in the environment, "
                "or define it in .env.local for local testing."
            )

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform an authenticated GET request to the X API v2."""
        url = f"{X_API_BASE}/{endpoint.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "User-Agent": "BuyTheRumor-Collector/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error("X API HTTP error %d on %s: %s", e.code, url, err_body)
            raise TwitterAPIError(e.code, err_body) from e
        except urllib.error.URLError as e:
            logger.error("X API network error on %s: %s", url, e)
            raise TwitterAPIError(0, str(e)) from e

    def get_users_by_usernames(self, usernames: list[str]) -> dict[str, dict[str, str]]:
        """Look up user IDs for a list of usernames in batches of up to 100.

        Returns a dictionary mapping lowercased username to user metadata:
        {
            "example_user": {"id": "1234567890", "name": "Example User", "username": "Example_User"}
        }
        """
        clean_names = [u.lstrip("@").strip() for u in usernames if u.strip()]
        user_map: dict[str, dict[str, str]] = {}
        if not clean_names:
            return user_map

        # X API allows up to 100 usernames per request
        chunk_size = 100
        for i in range(0, len(clean_names), chunk_size):
            chunk = clean_names[i : i + chunk_size]
            params = {
                "usernames": ",".join(chunk),
                "user.fields": "id,name,username",
            }
            try:
                resp = self._request("users/by", params=params)
                for user_data in resp.get("data", []):
                    uname = user_data["username"].lower()
                    user_map[uname] = {
                        "id": str(user_data["id"]),
                        "name": user_data.get("name", ""),
                        "username": user_data["username"],
                    }
            except TwitterAPIError as e:
                logger.warning("Failed to lookup batch of users: %s", e)

        return user_map

    def get_user_tweets(
        self,
        user_id: str,
        days: int = 3,
        max_results: int = 100,
        now: datetime | None = None,
        include_retweets: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch tweets for a user created within the last `days` days.

        Expands referenced tweets (quotes and retweets) so the full text
        of the referenced content is included.
        """
        current_time = now or datetime.now(timezone.utc)
        start_time = (current_time - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "start_time": start_time,
            "max_results": max(5, min(max_results, 100)),
            "tweet.fields": "created_at,text,public_metrics,entities,referenced_tweets",
            "expansions": "referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields": "username,name",
        }
        if not include_retweets:
            params["exclude"] = "retweets"

        try:
            resp = self._request(f"users/{user_id}/tweets", params=params)
            raw_tweets = resp.get("data", [])
            includes = resp.get("includes", {})

            # Build lookup maps for referenced tweets and their authors
            ref_tweets_map: dict[str, dict[str, Any]] = {str(t["id"]): t for t in includes.get("tweets", [])}
            ref_users_map: dict[str, str] = {
                str(u["id"]): u.get("username", "") for u in includes.get("users", [])
            }

            processed_tweets: list[dict[str, Any]] = []
            for t in raw_tweets:
                tweet_text = t.get("text", "")
                referenced_details: list[dict[str, Any]] = []
                quoted_parts: list[str] = []
                retweeted_text: str | None = None

                for ref in t.get("referenced_tweets", []):
                    ref_id = str(ref.get("id"))
                    ref_type = ref.get("type")
                    ref_tweet = ref_tweets_map.get(ref_id)
                    if ref_tweet:
                        ref_author_id = str(ref_tweet.get("author_id", ""))
                        ref_author_username = ref_users_map.get(ref_author_id, "unknown")
                        ref_body = ref_tweet.get("text", "")
                        referenced_details.append(
                            {
                                "type": ref_type,
                                "id": ref_id,
                                "author_username": ref_author_username,
                                "text": ref_body,
                            }
                        )
                        if ref_type == "quoted":
                            quoted_parts.append(f"[Quoting @{ref_author_username}: {ref_body}]")
                        elif ref_type == "retweeted":
                            retweeted_text = f"[Retweet @{ref_author_username}]: {ref_body}"

                # Stitch full text:
                if retweeted_text:
                    full_text = retweeted_text
                elif quoted_parts:
                    full_text = f"{tweet_text}\n" + "\n".join(quoted_parts)
                else:
                    full_text = tweet_text

                processed_tweets.append(
                    {
                        "id": t.get("id"),
                        "text": full_text,
                        "raw_text": tweet_text,
                        "created_at": t.get("created_at"),
                        "public_metrics": t.get("public_metrics", {}),
                        "referenced_tweets": referenced_details,
                    }
                )

            return processed_tweets
        except TwitterAPIError as e:
            if e.status_code == 429:
                logger.warning("Rate limit reached on tweets for user %s; skipping", user_id)
            else:
                logger.warning("Error fetching tweets for user %s: %s", user_id, e)
            return []

    def collect_tweets(
        self,
        accounts: list[str],
        days: int = 3,
        max_results_per_user: int = 100,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Collect tweets from all specified accounts for the past `days` days.

        Returns a structured dictionary with all tweets, source references,
        and collection metadata.
        """
        current_time = now or datetime.now(timezone.utc)
        user_map = self.get_users_by_usernames(accounts)
        all_tweets: list[dict[str, Any]] = []

        source_idx = 1
        for account in accounts:
            clean_name = account.lstrip("@").strip().lower()
            user_info = user_map.get(clean_name)
            if not user_info:
                logger.info("Could not resolve user ID for account %s; skipping", account)
                continue

            user_id = user_info["id"]
            tweets = self.get_user_tweets(
                user_id=user_id,
                days=days,
                max_results=max_results_per_user,
                now=current_time,
            )

            for t in tweets:
                source_id = f"X{source_idx}"
                source_idx += 1
                tweet_id = str(t.get("id"))
                tweet_url = f"https://x.com/{user_info['username']}/status/{tweet_id}"

                all_tweets.append(
                    {
                        "source_id": source_id,
                        "tweet_id": tweet_id,
                        "author_id": user_id,
                        "author_username": user_info["username"],
                        "author_name": user_info["name"],
                        "created_at": t.get("created_at"),
                        "text": t.get("text", ""),
                        "raw_text": t.get("raw_text", t.get("text", "")),
                        "referenced_tweets": t.get("referenced_tweets", []),
                        "metrics": t.get("public_metrics", {}),
                        "url": tweet_url,
                    }
                )

        # Sort all collected tweets chronologically descending
        all_tweets.sort(key=lambda t: t.get("created_at") or "", reverse=True)

        return {
            "collected_at": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days_lookback": days,
            # Lets a later job tell "this account posted nothing" from "this account wasn't fetched".
            "accounts": [a.lstrip("@").strip() for a in accounts],
            "total_accounts_requested": len(accounts),
            "accounts_resolved": len(user_map),
            "total_tweets": len(all_tweets),
            "tweets": all_tweets,
        }


def collect_and_save_tweets(
    storage: S3DataStorage | None = None,
    days: int = 3,
    max_results_per_user: int = 100,
) -> tuple[dict[str, Any], str]:
    """Collect tweets from every followed account (config/sources.json) and save them to S3.

    Returns (collection_data, s3_key).
    """
    s3_storage = storage or S3DataStorage()
    accounts = [s.handle for s in load_sources(s3_storage, load_settings())]

    collector = TwitterCollector()
    logger.info("Collecting tweets for %d accounts (last %d days)...", len(accounts), days)
    data = collector.collect_tweets(accounts=accounts, days=days, max_results_per_user=max_results_per_user)

    timestamp_folder = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    s3_key = s3_storage.save_batch(
        data=data,
        timestamp=timestamp_folder,
        filename="tweets.json",
    )
    logger.info("Saved %d tweets to S3: %s", data["total_tweets"], s3_key)

    return data, s3_key
