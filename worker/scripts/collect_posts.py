#!/usr/bin/env python3
"""Collect recent posts from every followed account and upload the batch to S3."""

import argparse
import logging
import sys
from pathlib import Path

# Ensure worker/ directory is in sys.path
worker_dir = Path(__file__).resolve().parent.parent
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from app.collector import PostCollector, collect_and_save_posts, load_collector_config  # noqa: E402
from app.contract import JobFailure  # noqa: E402
from app.sources import load_sources  # noqa: E402
from app.storage import S3DataStorage, load_settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Collect posts from the accounts in config/sources.json")
    parser.add_argument("--days", type=int, default=3, help="Days of history to fetch (default: 3)")
    parser.add_argument(
        "--limit-accounts", type=int, default=None, help="Limit number of accounts to fetch (for testing)"
    )
    parser.add_argument("--no-s3", action="store_true", help="Print collected posts without uploading to S3")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("Buy the Rumor — Post Collection")
    print("=" * 60)

    storage = S3DataStorage()
    try:
        settings = load_settings()
        accounts = [s.handle for s in load_sources(storage, settings)]
        config = load_collector_config(storage, settings)
    except JobFailure:
        print("[ERROR] Could not load the private config from the bucket (see log above)", file=sys.stderr)
        sys.exit(1)

    if args.limit_accounts:
        accounts = accounts[: args.limit_accounts]
    print(f"Tracking {len(accounts)} account(s) for the last {args.days} days.")

    if args.no_s3:
        data = PostCollector(config).collect_posts(accounts=accounts, days=args.days)
        print(
            f"\n[DONE] Collected {data['total_posts']} posts "
            f"from {data['accounts_resolved']} resolved accounts."
        )
        for p in data["posts"][:5]:
            print(f"\n[{p['source_id']}] @{p['author_username']} ({p['created_at']}):")
            print(f"  {p['text'][:120]}...")
    else:
        print(f"\nTarget S3 Bucket: {storage.bucket_name}")
        data, s3_key = collect_and_save_posts(storage=storage, days=args.days)
        print(f"\n[SUCCESS] Collected {data['total_posts']} posts.")
        print(f"Uploaded to S3: s3://{storage.bucket_name}/{s3_key}")


if __name__ == "__main__":
    main()
