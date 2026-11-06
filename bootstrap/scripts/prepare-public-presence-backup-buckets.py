#!/usr/bin/env python3
"""Plan, then provision protected S3 buckets for Public Presence recovery.

This operator command changes only bucket metadata or creates the named backup
bucket. It never reads, copies or deletes objects. Without --apply it is read-only.
"""

import argparse
import configparser
import json
import sys
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as exc:
    raise SystemExit("python3-boto3 is required") from exc


def client_from_rclone(path: Path, remote: str):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path) or not parser.has_section(remote):
        raise ValueError("operator rclone configuration is missing")
    section = parser[remote]
    if section.get("type") != "s3" or not section.get("endpoint", "").startswith("https://"):
        raise ValueError("operator remote must be S3 over HTTPS")
    return boto3.client(
        "s3",
        endpoint_url=section["endpoint"],
        aws_access_key_id=section["access_key_id"],
        aws_secret_access_key=section["secret_access_key"],
        region_name="default",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "unknown")


def bucket_exists(client, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if error_code(exc) in {"404", "NoSuchBucket"}:
            return False
        raise


def bucket_lock(client, bucket: str):
    try:
        return client.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
    except ClientError as exc:
        if error_code(exc) in {"ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration"}:
            return None
        raise


def versioned(client, bucket: str) -> bool:
    return client.get_bucket_versioning(Bucket=bucket).get("Status") == "Enabled"


def plan(client, backup_bucket: str, assets_bucket: str, days: int):
    if backup_bucket == assets_bucket:
        raise ValueError("backup and asset buckets must be different")
    actions = []
    exists = bucket_exists(client, backup_bucket)
    if not exists:
        actions.append("create_backup_bucket_with_object_lock")
        actions.append("enable_backup_versioning")
        actions.append("set_backup_compliance_retention")
    else:
        lock = bucket_lock(client, backup_bucket)
        if not lock or lock.get("ObjectLockEnabled") != "Enabled":
            raise ValueError("existing backup bucket lacks Object Lock; choose a new name")
        retention = lock.get("Rule", {}).get("DefaultRetention", {})
        if not retention:
            actions.append("set_backup_compliance_retention")
        elif retention.get("Mode") != "COMPLIANCE" or retention.get("Days", 0) < days:
            raise ValueError("existing backup retention differs; review it manually")
        if not versioned(client, backup_bucket):
            actions.append("enable_backup_versioning")
    if not bucket_exists(client, assets_bucket):
        raise ValueError("asset bucket is missing")
    if not versioned(client, assets_bucket):
        actions.append("enable_asset_versioning")
    return actions


def apply(client, actions, backup_bucket: str, assets_bucket: str, days: int):
    for action in actions:
        if action == "create_backup_bucket_with_object_lock":
            client.create_bucket(Bucket=backup_bucket, ObjectLockEnabledForBucket=True)
        elif action == "set_backup_compliance_retention":
            client.put_object_lock_configuration(
                Bucket=backup_bucket,
                ObjectLockConfiguration={
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": days}},
                },
            )
        elif action == "enable_backup_versioning":
            client.put_bucket_versioning(
                Bucket=backup_bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        elif action == "enable_asset_versioning":
            client.put_bucket_versioning(
                Bucket=assets_bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        else:
            raise ValueError("unknown planned action")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--remote", default="s3")
    parser.add_argument("--backup-bucket", required=True)
    parser.add_argument("--assets-bucket", default="rbx-presence")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.retention_days < 30:
        parser.error("compliance retention must be at least 30 days")
    client = client_from_rclone(args.config, args.remote)
    actions = plan(client, args.backup_bucket, args.assets_bucket, args.retention_days)
    if not args.apply:
        print(json.dumps({"mode": "plan", "actions": actions, "backup_bucket": args.backup_bucket}))
        return 0
    apply(client, actions, args.backup_bucket, args.assets_bucket, args.retention_days)
    remaining = plan(client, args.backup_bucket, args.assets_bucket, args.retention_days)
    print(json.dumps({"mode": "apply", "actions": actions, "remaining": remaining}))
    return 0 if not remaining else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyError, ValueError, ClientError) as exc:
        print(json.dumps({"result": "blocked", "error_type": type(exc).__name__}))
        sys.exit(2)
