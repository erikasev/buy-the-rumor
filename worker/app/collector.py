"""Collects recent posts from the followed accounts through the source's REST API.

Which source we use is private (docs/adr/0001-data-sources-stay-internal.md), so the API's
base URL, endpoint paths, query parameters and response field names are not in the code.
They come from the private `config/collector.json` in the bucket (see CollectorConfig).
The token is SOURCES_API_TOKEN: a Lambda environment variable, or .env.local locally.

The API is expected to follow a common shape: an account lookup by handle, a per-account
timeline, and responses of the form {"data": [...], "includes": {...}}.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_secret, load_settings
from app.sources import load_private, load_sources
from app.storage import S3DataStorage

logger = logging.getLogger(__name__)

TOKEN_ENV = "SOURCES_API_TOKEN"
CONFIG_KEY = "config/collector.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookupConfig(_Strict):
    path: str  # resolves handles to account ids
    names_param: str  # query parameter taking a comma-separated list of handles
    batch_size: int = Field(default=100, ge=1, le=100)
    params: dict[str, str] = Field(default_factory=dict)


class TimelineConfig(_Strict):
    path: str  # per-account posts; contains "{account_id}"
    since_param: str
    limit_param: str
    min_limit: int = Field(default=5, ge=1)
    max_limit: int = Field(default=100, ge=1)
    params: dict[str, str] = Field(default_factory=dict)


class FieldNames(_Strict):
    references: str  # on a post: the posts it quotes or reposts
    included_posts: str  # under "includes": the referenced posts
    included_accounts: str  # under "includes": their authors
    metrics: str  # on a post: engagement counts
    quote: str  # reference type of a quote
    repost: str  # reference type of a repost


class CollectorConfig(_Strict):
    """The private description of the source API (config/collector.json)."""

    version: int = Field(ge=1, le=1)
    base_url: str = Field(pattern=r"^https://")
    post_url: str  # link to a post: contains "{username}" and "{id}"
    lookup: LookupConfig
    timeline: TimelineConfig
    fields: FieldNames

    @property
    def post_host(self) -> str:
        """The source's web host, e.g. for keeping it out of user-facing text."""
        return urllib.parse.urlparse(self.post_url).netloc


def load_collector_config(storage: Any, settings: dict[str, Any]) -> CollectorConfig:
    return load_private(storage, settings, "collector", CONFIG_KEY, CollectorConfig)


def get_sources_token() -> str | None:
    """The source API token from the environment (Lambda), or .env.local when run locally."""
    return get_secret(TOKEN_ENV)


class SourceAPIError(RuntimeError):
    """Raised when a source API call fails."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Source API error (HTTP {status_code}): {message}")
        self.status_code = status_code
        self.message = message


class PostCollector:
    """Collects recent posts from the followed accounts."""

    def __init__(self, config: CollectorConfig, token: str | None = None):
        self.config = config
        self.token = token or get_sources_token()
        if not self.token:
            raise ValueError(
                f"{TOKEN_ENV} not found. Set it in the environment, "
                "or define it in .env.local for local testing."
            )

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Authenticated GET against the source API."""
        url = f"{self.config.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "BuyTheRumor-Collector/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error("Source API HTTP error %d on %s: %s", e.code, endpoint, err_body)
            raise SourceAPIError(e.code, err_body) from e
        except urllib.error.URLError as e:
            logger.error("Source API network error on %s: %s", endpoint, e)
            raise SourceAPIError(0, str(e)) from e

    def lookup_accounts(self, handles: list[str]) -> dict[str, dict[str, str]]:
        """Resolve handles to account ids, in batches.

        Returns lowercased handle -> {"id": ..., "name": ..., "username": ...}, e.g.
        {"example_user": {"id": "1234567890", "name": "Example User", "username": "Example_User"}}
        """
        lookup = self.config.lookup
        names = [h.lstrip("@").strip() for h in handles if h.strip()]
        accounts: dict[str, dict[str, str]] = {}
        for i in range(0, len(names), lookup.batch_size):
            chunk = names[i : i + lookup.batch_size]
            try:
                resp = self._request(
                    lookup.path, params={lookup.names_param: ",".join(chunk), **lookup.params}
                )
            except SourceAPIError as e:
                logger.warning("Failed to look up a batch of accounts: %s", e)
                continue
            for a in resp.get("data", []):
                accounts[a["username"].lower()] = {
                    "id": str(a["id"]),
                    "name": a.get("name", ""),
                    "username": a["username"],
                }
        return accounts

    def get_account_posts(
        self, account_id: str, days: int = 3, max_results: int = 100, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Posts by one account from the last `days` days.

        Quoted and reposted content is expanded, so a post's text includes what it refers to.
        """
        timeline, f = self.config.timeline, self.config.fields
        current_time = now or datetime.now(timezone.utc)
        params = {
            timeline.since_param: (current_time - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeline.limit_param: max(timeline.min_limit, min(max_results, timeline.max_limit)),
            **timeline.params,
        }
        try:
            resp = self._request(timeline.path.format(account_id=account_id), params=params)
        except SourceAPIError as e:
            if e.status_code == 429:
                logger.warning("Rate limit reached on posts for account %s; skipping", account_id)
            else:
                logger.warning("Error fetching posts for account %s: %s", account_id, e)
            return []

        includes = resp.get("includes", {})
        ref_posts = {str(p["id"]): p for p in includes.get(f.included_posts, [])}
        ref_authors = {str(a["id"]): a.get("username", "") for a in includes.get(f.included_accounts, [])}

        posts: list[dict[str, Any]] = []
        for p in resp.get("data", []):
            text = p.get("text", "")
            references: list[dict[str, Any]] = []
            quoted: list[str] = []
            reposted: str | None = None
            for ref in p.get(f.references, []):
                ref_id, ref_type = str(ref.get("id")), ref.get("type")
                ref_post = ref_posts.get(ref_id)
                if not ref_post:
                    continue
                author = ref_authors.get(str(ref_post.get("author_id", "")), "unknown")
                body = ref_post.get("text", "")
                references.append({"type": ref_type, "id": ref_id, "author_username": author, "text": body})
                if ref_type == f.quote:
                    quoted.append(f"[Quoting @{author}: {body}]")
                elif ref_type == f.repost:
                    reposted = f"[Repost of @{author}]: {body}"

            if reposted:
                full_text = reposted
            elif quoted:
                full_text = text + "\n" + "\n".join(quoted)
            else:
                full_text = text
            posts.append(
                {
                    "id": p.get("id"),
                    "text": full_text,
                    "raw_text": text,
                    "created_at": p.get("created_at"),
                    "metrics": p.get(f.metrics, {}),
                    "references": references,
                }
            )
        return posts

    def collect_posts(
        self,
        accounts: list[str],
        days: int = 3,
        max_results_per_account: int = 100,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Posts from all `accounts` for the past `days` days, newest first, labeled S1..Sn."""
        current_time = now or datetime.now(timezone.utc)
        resolved = self.lookup_accounts(accounts)
        all_posts: list[dict[str, Any]] = []
        for handle in accounts:
            info = resolved.get(handle.lstrip("@").strip().lower())
            if not info:
                logger.info("Could not resolve account %s; skipping", handle)
                continue
            for p in self.get_account_posts(info["id"], days, max_results_per_account, current_time):
                post_id = str(p.get("id"))
                all_posts.append(
                    {
                        "source_id": f"S{len(all_posts) + 1}",
                        "post_id": post_id,
                        "author_id": info["id"],
                        "author_username": info["username"],
                        "author_name": info["name"],
                        "created_at": p.get("created_at"),
                        "text": p.get("text", ""),
                        "raw_text": p.get("raw_text", p.get("text", "")),
                        "references": p.get("references", []),
                        "metrics": p.get("metrics", {}),
                        "url": self.config.post_url.format(username=info["username"], id=post_id),
                    }
                )
        all_posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
        return {
            "collected_at": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days_lookback": days,
            # Lets a later job tell "this account posted nothing" from "this account wasn't fetched".
            "accounts": [a.lstrip("@").strip() for a in accounts],
            "total_accounts_requested": len(accounts),
            "accounts_resolved": len(resolved),
            "total_posts": len(all_posts),
            "posts": all_posts,
        }


def collect_and_save_posts(
    storage: S3DataStorage | None = None, days: int = 3, max_results_per_account: int = 100
) -> tuple[dict[str, Any], str]:
    """Collect posts from every followed account (config/sources.json) and save them to S3.

    Returns (collection_data, s3_key).
    """
    s3_storage = storage or S3DataStorage()
    settings = load_settings()
    accounts = [s.handle for s in load_sources(s3_storage, settings)]
    collector = PostCollector(load_collector_config(s3_storage, settings))
    logger.info("Collecting posts for %d accounts (last %d days)...", len(accounts), days)
    data = collector.collect_posts(
        accounts=accounts, days=days, max_results_per_account=max_results_per_account
    )
    timestamp_folder = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    s3_key = s3_storage.save_batch(data=data, timestamp=timestamp_folder, filename="posts.json")
    logger.info("Saved %d posts to S3: %s", data["total_posts"], s3_key)
    return data, s3_key
