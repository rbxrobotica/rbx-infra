#!/usr/bin/env python3
"""Plan explicit denial of the backup user on every non-backup S3 bucket.

Contabo's S3 Read and Write sub-user role spans Object Storage. Its documented
bucket-policy method restricts a user by adding a Deny statement per bucket.
This command preserves existing policy statements and is read-only by default.
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

from botocore.exceptions import ClientError


SCRIPT = Path(__file__).with_name("prepare-public-presence-backup-buckets.py")
SPEC = importlib.util.spec_from_file_location("prepare_backup_buckets", SCRIPT)
BUCKETS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUCKETS)

SID = "DenyRbxPostgresBackupUser"
ARN = re.compile(r"^arn:aws:iam::[A-Za-z0-9-]+:user/[A-Za-z0-9-]+:[A-Za-z0-9-]+$")


def existing_policy(client, bucket: str) -> dict:
    try:
        policy = json.loads(client.get_bucket_policy(Bucket=bucket)["Policy"])
    except ClientError as exc:
        if BUCKETS.error_code(exc) in {"NoSuchBucketPolicy", "NoSuchBucketPolicyConfiguration"}:
            return {"Version": "2012-10-17", "Statement": []}
        raise
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        policy["Statement"] = [statements]
    elif not isinstance(statements, list):
        raise ValueError(f"invalid policy statements on {bucket}")
    return policy


def denial(principal_arn: str) -> dict:
    return {
        "Sid": SID,
        "Effect": "Deny",
        "Principal": {"AWS": [principal_arn]},
        "Action": "*",
        "Resource": "*",
    }


def plan(client, backup_bucket: str, principal_arn: str):
    if not ARN.fullmatch(principal_arn):
        raise ValueError("principal must be a Contabo S3 user ARN")
    buckets = sorted(bucket["Name"] for bucket in client.list_buckets()["Buckets"])
    if backup_bucket not in buckets:
        raise ValueError("backup bucket does not exist")
    changes = []
    expected = denial(principal_arn)
    for bucket in buckets:
        if bucket == backup_bucket:
            continue
        policy = existing_policy(client, bucket)
        same_sid = [s for s in policy["Statement"] if s.get("Sid") == SID]
        if same_sid:
            if same_sid != [expected]:
                raise ValueError(f"conflicting managed policy statement on {bucket}")
            continue
        policy["Statement"].append(expected)
        changes.append((bucket, policy))
    return changes, len(buckets) - 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--remote", default="s3")
    parser.add_argument("--backup-bucket", required=True)
    parser.add_argument("--backup-principal-arn", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = BUCKETS.client_from_rclone(args.config, args.remote)
    changes, other_bucket_count = plan(client, args.backup_bucket, args.backup_principal_arn)
    if not args.apply:
        print(json.dumps({"mode": "plan", "other_bucket_count": other_bucket_count,
                          "buckets_to_restrict": [name for name, _ in changes]}))
        return 0
    for bucket, policy in changes:
        client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy, separators=(",", ":")))
    remaining, _ = plan(client, args.backup_bucket, args.backup_principal_arn)
    print(json.dumps({"mode": "apply", "buckets_restricted": [name for name, _ in changes],
                      "remaining": [name for name, _ in remaining]}))
    return 0 if not remaining else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyError, ValueError, ClientError) as exc:
        print(json.dumps({"result": "blocked", "error_type": type(exc).__name__}))
        sys.exit(2)
