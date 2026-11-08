"""Adversarial offline contracts; no real B2 account or credentials are used."""
import contextlib
import dataclasses
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import b2_preflight as contract


def encoded(value, status=200):
    return contract.Response(status, json.dumps(value).encode())


class B2MetadataContractTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.transport = contract.fixture_inputs()
        self.auth = json.loads(self.transport.authorization.body)
        self.buckets = json.loads(self.transport.buckets.body)

    def invoke(self):
        self.transport.authorization = encoded(self.auth)
        self.transport.buckets = encoded(self.buckets)
        return contract.simulate_preflight(self.profile, self.transport)

    def test_mock_contract_keeps_real_qualification_false_and_does_not_reveal_identities(self):
        with mock.patch("socket.socket", side_effect=AssertionError("Network prohibited")):
            result = self.invoke()
        self.assertEqual(result["mode"], "simulation_only")
        self.assertTrue(result["injected_metadata_contract_matched"])
        for key in ("real_account_qualified", "live_b2_access_performed", "anonymous_access_denial_tested",
                    "object_lock_enforcement_tested", "upload_or_restore_tested", "retention_changed"):
            self.assertIs(result[key], False)
        for identity in (self.profile.account_id, self.profile.bucket_id, self.profile.bucket_name):
            self.assertNotIn(identity, json.dumps(result))
        self.assertEqual(self.transport.calls, [
            ("authorize_v4",), ("list_buckets_v4", self.profile.api_origin,
                                 {"accountId": self.profile.account_id, "bucketId": self.profile.bucket_id})])

    def test_unrestricted_extra_and_unknown_capabilities_stop_before_bucket_lookup(self):
        for caps in ([], ["all"], ["listBuckets"],
                     sorted(contract.CAPABILITIES) + ["deleteFiles"],
                     sorted(contract.CAPABILITIES) + ["bypassGovernance"],
                     sorted(contract.CAPABILITIES) + ["writeBuckets"],
                     sorted(contract.CAPABILITIES) + ["newUnknownCapability"],
                     ["listBuckets", "listBuckets"], None):
            with self.subTest(caps=caps):
                self.setUp()
                self.auth["apiInfo"]["storageApi"]["allowed"]["capabilities"] = caps
                with self.assertRaisesRegex(contract.ContractError, "authorization_contract_mismatch"):
                    self.invoke()
                self.assertEqual(self.transport.calls, [("authorize_v4",)])

    def test_bucket_restriction_must_be_exact_single_known_bucket(self):
        for buckets in (None, [], [{"id": self.profile.bucket_id, "name": None}],
                        [{"id": "other-fixture", "name": self.profile.bucket_name}],
                        [{"id": self.profile.bucket_id, "name": self.profile.bucket_name}] * 2):
            with self.subTest(buckets=buckets):
                self.setUp()
                self.auth["apiInfo"]["storageApi"]["allowed"]["buckets"] = buckets
                with self.assertRaises(contract.ContractError):
                    self.invoke()
                self.assertEqual(self.transport.calls, [("authorize_v4",)])

    def test_prefix_restriction_cannot_be_absent_broader_or_a_sibling(self):
        for prefix in (None, "", "rbx-resilience-canary/", self.profile.name_prefix.rstrip("/"),
                       self.profile.name_prefix + "other/", "rbx-resilience-canary/other-fixture/"):
            with self.subTest(prefix=prefix):
                self.setUp()
                self.auth["apiInfo"]["storageApi"]["allowed"]["namePrefix"] = prefix
                with self.assertRaises(contract.ContractError):
                    self.invoke()
                self.assertEqual(self.transport.calls, [("authorize_v4",)])

    def test_endpoint_substitution_stops_before_any_delegated_call(self):
        for field in ("apiUrl", "downloadUrl", "s3ApiUrl"):
            for origin in ("http://api001.backblazeb2.com", "https://eu2.contabostorage.com",
                           "https://api001.backblazeb2.com.evil.invalid", "https://api999.backblazeb2.com",
                           "https://user:fixture@api001.backblazeb2.com", "https://127.0.0.1"):
                with self.subTest(field=field, origin=origin):
                    self.setUp()
                    self.auth["apiInfo"]["storageApi"][field] = origin
                    with self.assertRaises(contract.ContractError):
                        self.invoke()
                    self.assertEqual(self.transport.calls, [("authorize_v4",)])

    def test_profiles_require_canonical_b2_origins_and_dedicated_run_prefix(self):
        for field, value in (("api_origin", "https://api001.backblazeb2.com:443"),
                             ("api_origin", "https://api001.backblazeb2.com/path"),
                             ("download_origin", "https://f001.backblazeb2.com?next=evil"),
                             ("s3_origin", "https://s3.us-west-001.backblazeb2.com#fragment"),
                             ("name_prefix", "production/backups/"),
                             ("name_prefix", "rbx-resilience-canary/../"),
                             ("name_prefix", "rbx-resilience-canary/run"),
                             ("account_id", "fixture\nheader")):
            with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                dataclasses.replace(self.profile, **{field: value})

    def test_legacy_v3_shape_and_missing_v4_fields_are_not_guessed(self):
        self.auth["apiInfo"]["storageApi"]["allowed"] = {
            "bucketId": self.profile.bucket_id, "bucketName": self.profile.bucket_name,
            "capabilities": sorted(contract.CAPABILITIES), "namePrefix": self.profile.name_prefix}
        with self.assertRaises(contract.ContractError):
            self.invoke()
        self.assertEqual(self.transport.calls, [("authorize_v4",)])
        for key in ("allowed", "apiUrl", "downloadUrl", "s3ApiUrl"):
            with self.subTest(missing=key):
                self.setUp()
                del self.auth["apiInfo"]["storageApi"][key]
                with self.assertRaises(contract.ContractError):
                    self.invoke()

    def test_other_accounts_public_unknown_and_missing_bucket_state_fail(self):
        for field, value in (("accountId", "other-fixture-account"), ("bucketId", "other-fixture-id"),
                             ("bucketName", "other-fixture-name"), ("bucketType", "allPublic"),
                             ("bucketType", "restricted"), ("bucketType", None), ("revision", True),
                             ("lifecycleRules", [{"daysFromUploadingToHiding": 1}])):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.buckets["buckets"][0][field] = value
                with self.assertRaises(contract.ContractError):
                    self.invoke()
        for key in ("bucketType", "revision", "lifecycleRules", "fileLockConfiguration"):
            with self.subTest(missing=key):
                self.setUp()
                del self.buckets["buckets"][0][key]
                with self.assertRaises(contract.ContractError):
                    self.invoke()

    def test_wrong_bucket_lookup_cardinality_is_not_an_authoritative_match(self):
        for buckets in (None, [], self.buckets["buckets"] * 2):
            with self.subTest(buckets=buckets):
                self.setUp()
                self.buckets["buckets"] = buckets
                with self.assertRaises(contract.ContractError):
                    self.invoke()

    def test_filtered_lock_metadata_or_unplanned_default_retention_fails_closed(self):
        for lock in ({"isClientAuthorizedToRead": False, "value": None},
                     {"isClientAuthorizedToRead": 1, "value": {"isFileLockEnabled": True}},
                     {"isClientAuthorizedToRead": True, "value": {"isFileLockEnabled": False}},
                     {"isClientAuthorizedToRead": True, "value": {"isFileLockEnabled": True}},
                     {"isClientAuthorizedToRead": True, "value": {"isFileLockEnabled": True,
                         "defaultRetention": {"mode": "governance", "period": {"duration": 1, "unit": "days"}}}}):
            with self.subTest(lock=lock):
                self.setUp()
                self.buckets["buckets"][0]["fileLockConfiguration"] = lock
                with self.assertRaises(contract.ContractError):
                    self.invoke()

    def test_response_bounds_duplicates_malformed_json_and_redirects_fail(self):
        for response in (contract.Response(302, b"untrusted diagnostic"),
                         contract.Response(403, b"untrusted diagnostic"),
                         contract.Response(True, b"{}"),
                         contract.Response(200, b"x" * (contract.MAX_RESPONSE_BYTES + 1)),
                         contract.Response(200, b'{"accountId":"first","accountId":"second"}'),
                         contract.Response(200, b"\xff"), contract.Response(200, b"[]"),
                         contract.Response(200, b"[" * 2000)):
            with self.subTest(status=response.status, length=len(response.body)):
                self.transport.authorization = response
                with self.assertRaises(contract.ContractError) as caught:
                    contract.simulate_preflight(self.profile, self.transport)
                self.assertNotIn("untrusted diagnostic", str(caught.exception))
                self.assertFalse(any(call[0] == "list_buckets_v4" for call in self.transport.calls))

    def test_injected_failures_and_ignored_sensitive_fields_never_reach_reports(self):
        marker = "simulated-sensitive-diagnostic-not-a-real-token"
        self.auth["authorizationToken"] = marker
        self.auth["unexpectedProviderDiagnostic"] = marker
        result = self.invoke()
        self.assertNotIn(marker, json.dumps(result))
        with mock.patch.object(self.transport, "authorize_v4", side_effect=RuntimeError(marker)):
            with self.assertRaisesRegex(contract.ContractError, "injected_transport_failed") as caught:
                contract.simulate_preflight(self.profile, self.transport)
        self.assertNotIn(marker, str(caught.exception))

    def test_cli_has_only_explicit_simulation_and_ignores_ambient_provider_settings(self):
        for arguments in ([], ["--live"], ["--upload", "anything"], ["--token", "fixture-not-secret"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(contract.main(arguments), 2)
            self.assertEqual(json.loads(output.getvalue())["mode"], "simulation_only")
            self.assertNotIn("fixture-not-secret", output.getvalue())
        with mock.patch.dict("os.environ", {"AWS_SECRET_ACCESS_KEY": "fixture-not-secret",
                                            "S3_ARCHIVE_ENDPOINT": "https://eu2.contabostorage.com"}), \
                mock.patch("socket.socket", side_effect=AssertionError("Network prohibited")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(contract.main(["--simulate"]), 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "simulation_only")


if __name__ == "__main__":
    unittest.main()
