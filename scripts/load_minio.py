"""Upload derived and raw GFS Parquet files to the local S3 bucket (RustFS, MinIO-compatible).

Zetaris cannot read files on the laptop, so it reads these objects as S3.
data/derived/*.parquet goes to s3://vogdesk/derived/ and
data/raw/gfs/*.parquet goes to s3://vogdesk/raw/gfs/.
Endpoint and credentials come from MINIO_ENDPOINT, MINIO_ROOT_USER and
MINIO_ROOT_PASSWORD (.env in the repo root is loaded if python-dotenv is installed).
Usage: python scripts/load_minio.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "vogdesk"
# (local directory relative to the repo root, object key prefix)
SOURCES = [
    (Path("data") / "derived", "derived/"),
    (Path("data") / "raw" / "gfs", "raw/gfs/"),
]


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)


def collect() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for rel_dir, prefix in SOURCES:
        for path in sorted((ROOT / rel_dir).glob("*.parquet")):
            items.append((path, f"{prefix}{path.name}"))
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list files without uploading")
    args = parser.parse_args()

    load_env()
    items = collect()
    if not items:
        print("no parquet files found under data/derived or data/raw/gfs")
        return

    client = None
    if not args.dry_run:
        import time

        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError, EndpointConnectionError

        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://localhost:9000"),
            aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "vogdesk"),
            aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "vogdesk-local-only"),
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        # The server may still be starting; wait up to a minute, then create the bucket if missing.
        for attempt in range(12):
            try:
                existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
                break
            except EndpointConnectionError:
                time.sleep(5)
        else:
            raise SystemExit("S3 endpoint not reachable after 60 s")
        if BUCKET not in existing:
            try:
                client.create_bucket(Bucket=BUCKET)
                print(f"created bucket {BUCKET}")
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise

    total = 0
    for path, key in items:
        size = path.stat().st_size
        total += size
        if client is not None:
            client.upload_file(str(path), BUCKET, key)
        print(f"{'would upload' if args.dry_run else 'uploaded'} s3://{BUCKET}/{key} {size} bytes")
    print(f"{len(items)} objects, {total} bytes{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
