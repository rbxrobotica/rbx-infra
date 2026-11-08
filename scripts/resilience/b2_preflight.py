#!/usr/bin/env python3
"""Simulate a B2 Native v4 metadata contract with injected fixture responses.

No HTTP implementation, credential loader, upload or retention mutation exists
here. The CLI only accepts --simulate and uses explicitly invented fixture IDs.
This module does not change the AWS/S3 preflight used by Comms and Plausible.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from typing import Protocol

API_VERSION = "v4"
CAPABILITIES = frozenset({"listBuckets", "readBucketRetentions"})
MAX_RESPONSE_BYTES = 64 * 1024
ORIGIN_PATTERNS = {
    "api": r"https://api[0-9]{3}\.backblazeb2\.com",
    "download": r"https://f[0-9]{3}\.backblazeb2\.com",
    "s3": r"https://s3\.[a-z]{2}-[a-z]+-[0-9]{3}\.backblazeb2\.com",
}


class ContractError(Exception):
    """Fixed error codes only; never include response bodies or identifiers."""


@dataclasses.dataclass(frozen=True)
class Profile:
    account_id: str = dataclasses.field(repr=False)
    bucket_id: str = dataclasses.field(repr=False)
    bucket_name: str = dataclasses.field(repr=False)
    name_prefix: str = dataclasses.field(repr=False)
    api_origin: str
    download_origin: str
    s3_origin: str

    def __post_init__(self):
        # These are a deliberately narrow tool policy, not universal B2 grammar.
        for value in (self.account_id, self.bucket_id):
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise ContractError("invalid_expected_identity")
        if (type(self.bucket_name) is not str
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{4,61}[a-z0-9]", self.bucket_name)):
            raise ContractError("invalid_expected_bucket_name")
        if (type(self.name_prefix) is not str
                or not re.fullmatch(r"rbx-resilience-canary/[a-z0-9-]{1,64}/", self.name_prefix)):
            raise ContractError("invalid_expected_canary_prefix")
        for role in ORIGIN_PATTERNS:
            value = getattr(self, role + "_origin")
            if type(value) is not str or not re.fullmatch(ORIGIN_PATTERNS[role], value):
                raise ContractError("invalid_expected_origin")


@dataclasses.dataclass(frozen=True)
class Response:
    status: int
    body: bytes = dataclasses.field(repr=False)


class MetadataTransport(Protocol):
    """An injectable test seam; no concrete network transport is supplied."""

    def authorize_v4(self) -> Response: ...

    def list_buckets_v4(self, api_origin: str, request: dict) -> Response: ...


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_json_field")
        result[key] = value
    return result


def _decode(response):
    if (type(response) is not Response or type(response.status) is not int
            or response.status != 200 or type(response.body) is not bytes):
        raise ContractError("metadata_response_not_successful")
    if not 0 < len(response.body) <= MAX_RESPONSE_BYTES:
        raise ContractError("metadata_response_out_of_bounds")
    try:
        value = json.loads(response.body.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("metadata_response_not_json") from None
    if type(value) is not dict:
        raise ContractError("metadata_response_not_object")
    return value


def _call(method, *arguments):
    try:
        response = method(*arguments)
    except Exception:
        # Injected failures can contain sensitive diagnostics. Do not echo them.
        raise ContractError("injected_transport_failed") from None
    return _decode(response)


def _authorization(profile, response):
    try:
        storage = response["apiInfo"]["storageApi"]
        allowed = storage["allowed"]
        buckets = allowed["buckets"]
        caps = allowed["capabilities"]
        valid = (
            response["accountId"] == profile.account_id
            and storage["apiUrl"] == profile.api_origin
            and storage["downloadUrl"] == profile.download_origin
            and storage["s3ApiUrl"] == profile.s3_origin
            and type(buckets) is list and len(buckets) == 1
            and type(buckets[0]) is dict
            and buckets[0] == {"id": profile.bucket_id, "name": profile.bucket_name}
            and allowed["namePrefix"] == profile.name_prefix
            and type(caps) is list and all(type(cap) is str for cap in caps)
            and len(caps) == len(CAPABILITIES) and frozenset(caps) == CAPABILITIES
        )
    except (KeyError, TypeError, IndexError):
        valid = False
    if not valid:
        raise ContractError("authorization_contract_mismatch")


def _bucket(profile, response):
    try:
        buckets = response["buckets"]
        if type(buckets) is not list or len(buckets) != 1 or type(buckets[0]) is not dict:
            raise ContractError("bucket_contract_mismatch")
        bucket = buckets[0]
        lock = bucket["fileLockConfiguration"]
        value = lock["value"]
        valid = (
            bucket["accountId"] == profile.account_id
            and bucket["bucketId"] == profile.bucket_id
            and bucket["bucketName"] == profile.bucket_name
            and bucket["bucketType"] == "allPrivate"
            and lock["isClientAuthorizedToRead"] is True
            and value["isFileLockEnabled"] is True
            and value["defaultRetention"] == {"mode": None, "period": None}
            and bucket["lifecycleRules"] == []
            and type(bucket["revision"]) is int and bucket["revision"] >= 0
        )
    except (KeyError, TypeError, IndexError):
        valid = False
    if not valid:
        raise ContractError("bucket_contract_mismatch")


def simulate_preflight(profile: Profile, transport: MetadataTransport):
    if type(profile) is not Profile:
        raise ContractError("explicit_profile_required")
    authorization = _call(transport.authorize_v4)
    _authorization(profile, authorization)
    # A restricted key must request only its known bucket; never enumerate all.
    response = _call(transport.list_buckets_v4, profile.api_origin,
                     {"accountId": profile.account_id, "bucketId": profile.bucket_id})
    _bucket(profile, response)
    return {"schema": "rbx.resilience.b2-native-metadata-simulation.v1",
            "mode": "simulation_only", "api_version": API_VERSION,
            "injected_metadata_contract_matched": True,
            "profile_role": "restricted_metadata_only",
            "capabilities": sorted(CAPABILITIES),
            "real_account_qualified": False, "live_b2_access_performed": False,
            "anonymous_access_denial_tested": False, "object_lock_enforcement_tested": False,
            "upload_or_restore_tested": False, "retention_changed": False,
            "production_data_accessed": False}


class FixtureTransport:
    """Static encoded snapshots with no HTTP client, credentials or environment."""

    def __init__(self, authorization: Response, buckets: Response):
        self.authorization = authorization
        self.buckets = buckets
        self.calls = []

    def authorize_v4(self):
        self.calls.append(("authorize_v4",))
        return self.authorization

    def list_buckets_v4(self, api_origin, request):
        self.calls.append(("list_buckets_v4", api_origin, dict(request)))
        return self.buckets


def fixture_inputs():
    """Invented IDs; URL examples follow the official v4 documentation only."""
    profile = Profile("fixture-account-not-real", "fixture-bucket-not-real",
                      "rbx-fixture-bucket-not-real", "rbx-resilience-canary/fixture-only/",
                      "https://api001.backblazeb2.com", "https://f001.backblazeb2.com",
                      "https://s3.us-west-001.backblazeb2.com")
    authorization = {"accountId": profile.account_id,
                     "apiInfo": {"storageApi": {
                         "apiUrl": profile.api_origin, "downloadUrl": profile.download_origin,
                         "s3ApiUrl": profile.s3_origin,
                         "allowed": {"buckets": [{"id": profile.bucket_id, "name": profile.bucket_name}],
                                     "capabilities": sorted(CAPABILITIES),
                                     "namePrefix": profile.name_prefix}}}}
    buckets = {"buckets": [{"accountId": profile.account_id, "bucketId": profile.bucket_id,
                            "bucketName": profile.bucket_name, "bucketType": "allPrivate",
                            "revision": 1, "lifecycleRules": [],
                            "fileLockConfiguration": {"isClientAuthorizedToRead": True, "value": {
                                "isFileLockEnabled": True,
                                "defaultRetention": {"mode": None, "period": None}}}}]}
    encode = lambda value: Response(200, json.dumps(value, sort_keys=True).encode())
    return profile, FixtureTransport(encode(authorization), encode(buckets))


def main(argv=None):
    if list(sys.argv[1:] if argv is None else argv) != ["--simulate"]:
        print(json.dumps({"mode": "simulation_only", "error": "only_simulation_is_implemented"}), file=sys.stderr)
        return 2
    try:
        profile, transport = fixture_inputs()
        print(json.dumps(simulate_preflight(profile, transport), sort_keys=True))
        return 0
    except ContractError as error:
        print(json.dumps({"mode": "simulation_only", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
