"""Offline checks for the read-only Public Presence recovery gate."""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


sys.modules.setdefault("boto3", types.ModuleType("boto3"))
botocore = sys.modules.setdefault("botocore", types.ModuleType("botocore"))
config = sys.modules.setdefault("botocore.config", types.ModuleType("botocore.config"))
exceptions = sys.modules.setdefault("botocore.exceptions", types.ModuleType("botocore.exceptions"))
config.Config = object
exceptions.ClientError = FakeClientError
botocore.config = config
botocore.exceptions = exceptions

source = Path(__file__).resolve().parents[1] / "check-public-presence-backup.py"
spec = importlib.util.spec_from_file_location("public_presence_backup_gate", source)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
prepare_source = Path(__file__).resolve().parents[1] / "prepare-public-presence-backup-buckets.py"
prepare_spec = importlib.util.spec_from_file_location("public_presence_backup_prepare", prepare_source)
prepare = importlib.util.module_from_spec(prepare_spec)
prepare_spec.loader.exec_module(prepare)
policy_source = Path(__file__).resolve().parents[1] / "prepare-postgres-backup-user-policies.py"
policy_spec = importlib.util.spec_from_file_location("public_presence_backup_policy", policy_source)
policies = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policies)


class FakePaginator:
    def __init__(self, prefixes):
        self.prefixes = prefixes

    def paginate(self, **_kwargs):
        yield {"CommonPrefixes": [{"Prefix": prefix} for prefix in self.prefixes]}


class FakeS3:
    def __init__(self, *, versioned=True, locked=True, files=None):
        self.versioned = versioned
        self.locked = locked
        self.files = files or {}

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": "Enabled" if self.versioned else "Suspended"}

    def get_object_lock_configuration(self, **_kwargs):
        if not self.locked:
            raise FakeClientError("ObjectLockConfigurationNotFoundError")
        return {"ObjectLockConfiguration": {
            "ObjectLockEnabled": "Enabled",
            "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 30}},
        }}

    def get_paginator(self, _name):
        return FakePaginator([
            "jaguar/postgres/2026-09-27T011502Z/",
            "jaguar/postgres/2026-09-28T011502Z/",
        ])

    def head_object(self, *, Key, **_kwargs):
        if Key not in self.files:
            raise FakeClientError("404")
        return {"ContentLength": self.files[Key]}


class RecoveryGateTests(unittest.TestCase):
    def test_lock_requires_compliance_retention(self):
        self.assertTrue(gate.bucket_locked(FakeS3(), "backup", 30))
        self.assertFalse(gate.bucket_locked(FakeS3(locked=False), "backup", 30))
        self.assertFalse(gate.bucket_locked(FakeS3(), "backup", 31))

    def test_latest_backup_requires_both_nonempty_files(self):
        prefix = "jaguar/postgres/2026-09-28T011502Z/"
        client = FakeS3(files={prefix + "globals.sql.gz": 100, prefix + "public_presence.dump": 200})
        self.assertEqual(gate.latest_backup(client, "backup"), ("2026-09-28T011502Z", True))
        client.files[prefix + "public_presence.dump"] = 0
        self.assertEqual(gate.latest_backup(client, "backup"), ("2026-09-28T011502Z", False))

    def test_asset_manifest_is_required_even_for_empty_bucket(self):
        stamp = "2026-09-28T011502Z"
        key = f"jaguar/presence-assets-status/{stamp}/manifest.txt"
        client = FakeS3(files={})
        self.assertFalse(gate.asset_snapshot_recorded(client, "backup", stamp))
        client.files[key] = 20
        self.assertTrue(gate.asset_snapshot_recorded(client, "backup", stamp))

    def test_backup_key_must_be_denied_on_other_buckets(self):
        operator = types.SimpleNamespace(list_buckets=lambda: {"Buckets": [
            {"Name": "backup"}, {"Name": "assets"},
        ]})

        class BackupKey:
            denied = True

            def list_objects_v2(self, **_kwargs):
                if self.denied:
                    raise FakeClientError("AccessDenied")
                return {"Contents": []}

        backup = BackupKey()
        self.assertTrue(gate.denied_on_other_buckets(backup, operator, "backup"))
        backup.denied = False
        self.assertFalse(gate.denied_on_other_buckets(backup, operator, "backup"))


class FakeBucketAdmin:
    def __init__(self):
        self.buckets = {"rbx-presence": {"versioned": False, "lock": None}}
        self.calls = []

    def head_bucket(self, *, Bucket):
        if Bucket not in self.buckets:
            raise FakeClientError("404")

    def get_bucket_versioning(self, *, Bucket):
        return {"Status": "Enabled" if self.buckets[Bucket]["versioned"] else "Suspended"}

    def get_object_lock_configuration(self, *, Bucket):
        lock = self.buckets[Bucket]["lock"]
        if lock is None:
            raise FakeClientError("ObjectLockConfigurationNotFoundError")
        return {"ObjectLockConfiguration": lock}

    def create_bucket(self, *, Bucket, ObjectLockEnabledForBucket):
        self.calls.append("create")
        self.buckets[Bucket] = {"versioned": False, "lock": {"ObjectLockEnabled": "Enabled"}}

    def put_bucket_versioning(self, *, Bucket, VersioningConfiguration):
        self.calls.append("versioning")
        self.buckets[Bucket]["versioned"] = VersioningConfiguration["Status"] == "Enabled"

    def put_object_lock_configuration(self, *, Bucket, ObjectLockConfiguration):
        self.calls.append("retention")
        self.buckets[Bucket]["lock"] = ObjectLockConfiguration


class ProvisionPlanTests(unittest.TestCase):
    def test_plan_is_read_only_then_apply_converges(self):
        client = FakeBucketAdmin()
        actions = prepare.plan(client, "rbx-postgres-recovery-eu2", "rbx-presence", 30)
        self.assertEqual(client.calls, [])
        self.assertEqual(actions, [
            "create_backup_bucket_with_object_lock", "enable_backup_versioning",
            "set_backup_compliance_retention", "enable_asset_versioning",
        ])
        prepare.apply(client, actions, "rbx-postgres-recovery-eu2", "rbx-presence", 30)
        self.assertEqual(prepare.plan(client, "rbx-postgres-recovery-eu2", "rbx-presence", 30), [])

    def test_existing_unlocked_bucket_is_refused(self):
        client = FakeBucketAdmin()
        client.buckets["old-backup"] = {"versioned": False, "lock": None}
        with self.assertRaises(ValueError):
            prepare.plan(client, "old-backup", "rbx-presence", 30)
        self.assertEqual(client.calls, [])


class FakePolicyAdmin:
    def __init__(self):
        self.policies = {
            "rbx-postgres-recovery-eu2": None,
            "rbx-presence": {"Version": "2012-10-17", "Statement": [{"Sid": "Existing", "Effect": "Allow"}]},
            "rbx-backups": None,
        }

    def list_buckets(self):
        return {"Buckets": [{"Name": name} for name in self.policies]}

    def get_bucket_policy(self, *, Bucket):
        policy = self.policies[Bucket]
        if policy is None:
            raise FakeClientError("NoSuchBucketPolicy")
        return {"Policy": json.dumps(policy)}

    def put_bucket_policy(self, *, Bucket, Policy):
        self.policies[Bucket] = json.loads(Policy)


class PolicyPlanTests(unittest.TestCase):
    def test_preserves_existing_policy_and_converges(self):
        client = FakePolicyAdmin()
        arn = "arn:aws:iam::5c37e60c3ee04f1eb116c436b1afadca:user/12345:3368c22e-08da-446f-a470-1928e58457a2"
        changes, count = policies.plan(client, "rbx-postgres-recovery-eu2", arn)
        self.assertEqual(count, 2)
        self.assertEqual(len(changes), 2)
        self.assertIsNone(client.policies["rbx-backups"])
        for bucket, policy in changes:
            client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        self.assertEqual(policies.plan(client, "rbx-postgres-recovery-eu2", arn)[0], [])
        self.assertEqual(client.policies["rbx-presence"]["Statement"][0]["Sid"], "Existing")

    def test_conflicting_managed_policy_is_rejected(self):
        client = FakePolicyAdmin()
        client.policies["rbx-presence"]["Statement"].append({"Sid": policies.SID, "Effect": "Allow"})
        arn = "arn:aws:iam::5c37e60c3ee04f1eb116c436b1afadca:user/12345:3368c22e-08da-446f-a470-1928e58457a2"
        with self.assertRaises(ValueError):
            policies.plan(client, "rbx-postgres-recovery-eu2", arn)


if __name__ == "__main__":
    unittest.main()
