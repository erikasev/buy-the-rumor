#!/usr/bin/env python3
"""Script to run X.com tweet collection and upload to S3."""

import argparse
import logging
import sys
from pathlib import Path

# Ensure worker/ directory is in sys.path
worker_dir = Path(__file__).resolve().parent.parent
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from app.contract import JobFailure  # noqa: E402
from app.sources import load_sources  # noqa: E402
from app.storage import S3DataStorage, load_settings  # noqa: E402
from app.twitter import TwitterCollector, collect_and_save_tweets  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Collect tweets from the accounts in config/sources.json")
    parser.add_argument("--days", type=int, default=3, help="Days of tweet history to fetch (default: 3)")
    parser.add_argument(
        "--limit-accounts", type=int, default=None, help="Limit number of accounts to fetch (for testing)"
    )
    parser.add_argument("--no-s3", action="store_true", help="Print collected tweets without uploading to S3")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("Buy the Rumor — Twitter/X Collection")
    print("=" * 60)

    try:
        accounts = [s.handle for s in load_sources(S3DataStorage(), load_settings())]
    except JobFailure:
        print("[ERROR] Could not load config/sources.json from the bucket (see log above)", file=sys.stderr)
        sys.exit(1)

    if args.limit_accounts:
        accounts = accounts[: args.limit_accounts]

    print(f"Tracking {len(accounts)} account(s) for the last {args.days} days:")
    for a in accounts[:10]:
        print(f"  - {a}")
    if len(accounts) > 10:
        print(f"  ... and {len(accounts) - 10} more.")

    collector = TwitterCollector()

    if args.no_s3:
        data = collector.collect_tweets(accounts=accounts, days=args.days)
        resolved = data["accounts_resolved"]
        print(f"\n[DONE] Collected {data['total_tweets']} tweets from {resolved} resolved accounts.")
        for t in data["tweets"][:5]:
            print(f"\n[{t['source_id']}] @{t['author_username']} ({t['created_at']}):")
            print(f"  {t['text'][:120]}...")
    else:
        storage = S3DataStorage()
        print(f"\nTarget S3 Bucket: {storage.bucket_name}")
        data, s3_key = collect_and_save_tweets(storage=storage, days=args.days)
        print(f"\n[SUCCESS] Collected {data['total_tweets']} tweets.")
        print(f"Uploaded to S3: s3://{storage.bucket_name}/{s3_key}")


if __name__ == "__main__":
    main()
