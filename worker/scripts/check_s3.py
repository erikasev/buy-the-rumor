#!/usr/bin/env python3
"""Convenience script to check S3 bucket and collected-data batches."""

import sys
from pathlib import Path

# Ensure worker/ directory is in sys.path
worker_dir = Path(__file__).resolve().parent.parent
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from app.storage import S3DataStorage  # noqa: E402


def main():
    print("=" * 60)
    print("Checking S3 collected data for Buy the Rumor...")
    print("=" * 60)
    try:
        storage = S3DataStorage()
        print(f"Bucket: {storage.bucket_name} (Region: {storage.region_name})")
        batches = storage.list_batches()
        print(f"Total batches found: {len(batches)}")

        if batches:
            print("\nBatches (latest first):")
            for i, b in enumerate(batches[:5]):
                print(f"  {i + 1}. Prefix: {b['prefix']}")
                print(f"     Timestamp: {b['timestamp_str']}")

            latest = storage.get_latest_batch(max_age_hours=24)
            if latest:
                print("\n[SUCCESS] Fresh data batch exists (within last 24h).")
                files = storage.read_batch_files(latest["prefix"])
                print(f"Found {len(files)} file(s) in latest batch:")
                for f in files:
                    print(f" - {f['filename']} (Key: {f['key']})")
            else:
                print("\n[NOTICE] Batches found, but none are fresh (<24h). Need new scrape.")
        else:
            print("\n[NOTICE] No collected-data batches found in S3 yet.")
            print("The worker will collect fresh posts on its next job.")

    except Exception as e:
        print(f"\n[ERROR] Failed to check S3: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
