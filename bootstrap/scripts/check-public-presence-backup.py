#!/usr/bin/env python3
"""Read-only S3 recovery gate for the Public Presence rollout.

Run on jaguar with its dedicated backup rclone configuration. The workload
access-key SHA-256 is supplied by the operator, never the key itself.
"""

import argparse
import configparser
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as exc:
    raise SystemExit("python3-boto3 is required") from exc


STAMP = re.compile(r"^jaguar/postgres/(\d{4}-\d{2}-\d{2}T\d{6}Z)/$")


def s3_client(config_path: Path, remote: str):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config_path) or not parser.has_section(remote):
        raise ValueError("backup rclone configuration is missing")
    section = parser[remote]
    if section.get("type") != "s3" or not section.get("endpoint", "").startswith("https://"):
        raise ValueError("backup remote must be S3 over HTTPS")
    access_key = section.get("access_key_id", "")
    secret_key = section.get("secret_access_key", "")
    if not access_key or not secret_key:
        raise ValueError("backup remote credentials are missing")
    client = boto3.client(
        "s3",
        endpoint_url=section["endpoint"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="eu-central-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return client, hashlib.sha256(access_key.encode()).hexdigest()


def bucket_versioned(client, bucket: str) -> bool:
    return client.get_bucket_versioning(Bucket=bucket).get("Status") == "Enabled"


def bucket_locked(client, bucket: str, min_days: int) -> bool:
    try:
        lock = client.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration"
        }:
            return False
        raise
    retention = lock.get("Rule", {}).get("DefaultRetention", {})
    return (
        lock.get("ObjectLockEnabled") == "Enabled"
        and retention.get("Mode") == "COMPLIANCE"
        and retention.get("Days", 0) >= min_days
    )


def latest_backup(client, bucket: str):
    paginator = client.get_paginator("list_objects_v2")
    stamps = []
    for page in paginator.paginate(Bucket=bucket, Prefix="jaguar/postgres/", Delimiter="/"):
        for item in page.get("CommonPrefixes", []):
            match = STAMP.match(item["Prefix"])
            if match:
                stamps.append(match.group(1))
    if not stamps:
        return None, False
    stamp = max(stamps)
    prefix = f"jaguar/postgres/{stamp}/"
    for name in ("globals.sql.gz", "public_presence.dump"):
        try:
            metadata = client.head_object(Bucket=bucket, Key=prefix + name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return stamp, False
            raise
        if metadata.get("ContentLength", 0) <= 0:
            return stamp, False
    return stamp, True


def asset_snapshot_recorded(client, bucket: str, stamp: str | None) -> bool:
    if not stamp:
        return False
    key = f"jaguar/presence-assets-status/{stamp}/manifest.txt"
    try:
        metadata = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            return False
        raise
    return metadata.get("ContentLength", 0) > 0


def denied_on_other_buckets(backup_client, operator_client, backup_bucket: str) -> bool:
    for item in operator_client.list_buckets().get("Buckets", []):
        if item["Name"] == backup_bucket:
            continue
        try:
            backup_client.list_objects_v2(Bucket=item["Name"], MaxKeys=1)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"AccessDenied", "403"}:
                continue
            raise
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/root/.config/rclone/postgres-backup.conf"))
    parser.add_argument("--remote", default="postgres-backup")
    parser.add_argument("--operator-config", type=Path, default=Path("/root/.config/rclone/rclone.conf"))
    parser.add_argument("--operator-remote", default="s3")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--assets-bucket", default="rbx-presence")
    parser.add_argument("--workload-access-key-sha256", required=True)
    parser.add_argument("--max-age-hours", type=int, default=36)
    parser.add_argument("--min-lock-days", type=int, default=30)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.workload_access_key_sha256):
        parser.error("workload access-key SHA-256 must be 64 lowercase hex characters")
    backup_client, backup_key_hash = s3_client(args.config, args.remote)
    operator_client, _ = s3_client(args.operator_config, args.operator_remote)
    stamp, has_required_files = latest_backup(backup_client, args.bucket)
    age_hours = None
    if stamp:
        created = datetime.strptime(stamp, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        age_hours = round((datetime.now(timezone.utc) - created).total_seconds() / 3600, 2)
    checks = {
        "distinct_backup_credential": backup_key_hash != args.workload_access_key_sha256,
        "backup_credential_denied_other_buckets": denied_on_other_buckets(
            backup_client, operator_client, args.bucket
        ),
        "backup_bucket_versioned": bucket_versioned(operator_client, args.bucket),
        "backup_bucket_compliance_lock": bucket_locked(operator_client, args.bucket, args.min_lock_days),
        "assets_bucket_versioned": bucket_versioned(operator_client, args.assets_bucket),
        "latest_backup_complete": has_required_files,
        "latest_assets_snapshot_recorded": asset_snapshot_recorded(backup_client, args.bucket, stamp),
        "latest_backup_fresh": age_hours is not None and 0 <= age_hours <= args.max_age_hours,
    }
    print(json.dumps({"checks": checks, "latest_backup": stamp, "age_hours": age_hours}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, ClientError) as exc:
        print(json.dumps({"error_type": type(exc).__name__, "result": "blocked"}))
        sys.exit(2)
