"""Offline-only adversarial tests. Fake bytes/tokens are not a live qualification."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.parse

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import b2_canary as c


class FakeB2:
    def __init__(self, profile, now):
        self.profile, self.now = profile, now
        self.calls, self.files = [], {}
        self.retention = {"mode": None, "period": None}
        self.auth_changes = None
        self.upload_changes = None
        self.lock_visible, self.file_lock_visible = True, True
        self.legal_hold = {"isClientAuthorizedToRead": True, "value": None}
        self.delete_error = "access_denied"
        self.wrong_download_identity = False
        self.expiry = int((now + dt.timedelta(hours=23)).timestamp() * 1000)

    def request(self, method, url, headers, data=None, limit=c.JSON_MAX):
        self.calls.append((method, url, dict(headers), data, limit))
        p = self.profile
        def result(value, status=200):
            return status, {}, c.canonical(value)
        if url == c.BOOTSTRAP:
            auth = {"accountId": p["accountId"], "authorizationToken": "fake-account-token",
                "applicationKeyExpirationTimestamp": self.expiry, "apiInfo": {"storageApi": {
                    "apiUrl": p["apiOrigin"], "downloadUrl": p["downloadOrigin"], "allowed": {
                    "buckets": [{"id": p["bucketId"], "name": p["bucketName"]}],
                    "namePrefix": p["prefix"], "capabilities": list(p["capabilities"])}}}}
            if self.auth_changes:
                self.auth_changes(auth)
            return result(auth)
        if url.endswith("b2_list_buckets"):
            assert json.loads(data) == {"accountId": p["accountId"], "bucketId": p["bucketId"]}
            return result({"buckets": [{"accountId": p["accountId"], "bucketId": p["bucketId"],
                "bucketName": p["bucketName"], "bucketType": "allPrivate", "lifecycleRules": [],
                "defaultServerSideEncryption": {"isClientAuthorizedToRead": True,
                    "value": {"mode": "SSE-B2", "algorithm": "AES256"}},
                "fileLockConfiguration": {"isClientAuthorizedToRead": self.lock_visible,
                    "value": {"isFileLockEnabled": True, "defaultRetention": self.retention}
                    if self.lock_visible else None}}]})
        if url.endswith("b2_get_upload_url"):
            assert json.loads(data) == {"bucketId": p["bucketId"]}
            return result({"bucketId": p["bucketId"], "authorizationToken": "fake-upload-token",
                "uploadUrl": "https://pod-000-1005-03.backblaze.com/b2api/v4/b2_upload_file?cvt=opaque-fixture&bucket=" + p["bucketId"]})
        if ".backblaze.com/b2api/v4/b2_upload_file" in url:
            assert headers["Authorization"] == "fake-upload-token"
            assert headers["X-Bz-Content-Sha1"] == hashlib.sha1(data).hexdigest()
            assert headers["Content-Length"] == str(len(data))
            name = urllib.parse.unquote(headers["X-Bz-File-Name"])
            key = "control" if name.endswith("control.gpg") else "locked"
            fid = "4_zfixture_" + key
            until = int(self.now.timestamp() * 1000) + 86400000 if key == "locked" else None
            self.files[fid] = (name, data)
            uploaded = {"accountId": p["accountId"], "bucketId": p["bucketId"], "fileId": fid,
                "fileName": name, "action": "upload", "contentLength": len(data),
                "contentSha1": hashlib.sha1(data).hexdigest(), "uploadTimestamp": int(self.now.timestamp() * 1000),
                "legalHold": self.legal_hold,
                "fileRetention": {"isClientAuthorizedToRead": self.file_lock_visible,
                    "value": {"mode": self.retention["mode"], "retainUntilTimestamp": until}
                    if self.file_lock_visible else None}}
            if self.upload_changes:
                self.upload_changes(uploaded)
            return result(uploaded)
        if "b2_download_file_by_id?" in url:
            fid = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["fileId"]
            name, body = self.files[fid]
            return 200, {"Content-Length": str(len(body)), "X-Bz-File-Id": "wrong-file" if self.wrong_download_identity else fid,
                "X-Bz-File-Name": urllib.parse.quote(name, safe=""), "X-Bz-Content-Sha1": hashlib.sha1(body).hexdigest()}, body
        if url.endswith("b2_delete_file_version"):
            args = json.loads(data)
            assert set(args) == {"fileId", "fileName"}
            if args["fileId"].endswith("locked"):
                return result({"status": 401, "code": self.delete_error, "message": "fake private diagnostic"}, 401)
            name, _ = self.files.pop(args["fileId"])
            assert args["fileName"] == name
            return result(args)
        raise AssertionError("Unexpected fake endpoint")


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(dt.timezone.utc)
        self.artifact = b"synthetic encrypted-artifact stand-in; GPG tested separately"
        self.profile = {"schema": "rbx.b2.canary-profile.v1", "accountId": "fixture-account",
            "bucketId": "fixture-bucket-id", "bucketName": "fixture-canary-bucket",
            "prefix": "rbx-resilience-canary/fixture-run/", "keyId": "fixture-key-not-account-id",
            "keyCreatedAtUTC": c.stamp(self.now - dt.timedelta(minutes=1)),
            "capabilities": sorted(c.APPROVED_CAPABILITIES), "apiOrigin": "https://api005.backblazeb2.com",
            "downloadOrigin": "https://f005.backblazeb2.com", "artifactSha256": c.sha256(self.artifact),
            "artifactSizeBytes": len(self.artifact), "syntheticOnly": True}
        self.credentials = {"keyId": self.profile["keyId"], "applicationKey": "fake-secret-not-real"}
        self.state, self.saved = {}, []
        self.transport = FakeB2(self.profile, self.now)

    def ui(self, locked=False):
        return {"schema": "rbx.b2.canary-ui.v1", "operatorAuthorized": True,
            **{key: self.profile[key] for key in ("accountId", "bucketId", "bucketName", "prefix")},
            "observedAtUTC": c.stamp(self.now - dt.timedelta(seconds=1)), "allPrivate": True,
            "keepAllVersions": True, "sseEnabled": True, "objectLockEnabled": True,
            "defaultRetention": {"mode": "compliance" if locked else None, "days": 1 if locked else None}}

    def invoke(self, phase, ui=None, artifact=None, downloaded=None):
        return c.run(phase, self.profile, self.credentials, self.state,
            lambda value: self.saved.append(copy.deepcopy(value)), self.transport,
            artifact=artifact, ui=ui, now=self.now, downloaded=downloaded)

    def uploaded_pair(self):
        self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
        self.transport.retention = {"mode": "compliance", "period": {"duration": 1, "unit": "days"}}
        self.transport.now = self.now
        self.invoke("locked-upload", ui=self.ui(True), artifact=self.artifact)

    def ready_for_delete(self):
        self.uploaded_pair()
        self.invoke("download-and-verify")
        self.invoke("delete-control")

    def test_complete_protocol_uses_separate_tokens_exact_versions_and_honest_report(self):
        with mock.patch("socket.socket", side_effect=AssertionError("No network permitted in unit tests")):
            self.ready_for_delete()
            captured = {}
            report = self.invoke("attempt-delete-locked", downloaded=lambda key, data: captured.update({key: data}))
        self.assertTrue(report["locked_delete_denied_and_readback_verified"])
        self.assertEqual(captured["locked-after-denial"], self.artifact)
        self.assertEqual(report["uploaded_bytes"], 2 * len(self.artifact))
        self.assertFalse(report["production_backup_qualified"])
        self.assertFalse(report["H1_qualified"])
        self.assertFalse(report["application_restore_tested"])
        receipt_text = json.dumps(self.saved)
        for forbidden in ("fake-secret-not-real", "fake-account-token", "fake-upload-token", "opaque-fixture"):
            self.assertNotIn(forbidden, receipt_text)
            self.assertNotIn(forbidden, json.dumps(report))
        for _, url, headers, _, _ in self.transport.calls:
            self.assertFalse(any("bypass" in key.lower() or "retention" in key.lower() or "legal" in key.lower() for key in headers))
            self.assertNotIn("b2_update", url)
        self.assertEqual(self.state["uploads"]["locked"]["fileRetention"]["mode"], "compliance")

    def test_key_id_is_not_the_account_id_and_mismatch_stops_before_network(self):
        self.credentials["keyId"] = self.profile["accountId"]
        with self.assertRaisesRegex(c.Stop, "key_id_mismatch"):
            self.invoke("authorize")
        self.assertEqual(self.transport.calls, [])

    def test_unknown_legal_hold_diagnostics_cannot_enter_private_receipt(self):
        marker = "fake-unexpected-secret-diagnostic"
        self.transport.legal_hold = {"isClientAuthorizedToRead": True, "value": {"diagnostic": marker}}
        self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.assertNotIn(marker, json.dumps(self.saved))
        self.assertEqual(self.state["uploads"]["control"]["legalHoldObservation"]["value"], "unknown")

    def test_old_or_future_upload_timestamp_does_not_establish_a_fresh_capture(self):
        for hours in (-48, 48):
            self.setUp()
            changed = int((self.now + dt.timedelta(hours=hours)).timestamp() * 1000)
            self.transport.upload_changes = lambda uploaded: uploaded.update(uploadTimestamp=changed)
            with self.assertRaisesRegex(c.Stop, "upload_timestamp_not_recent"):
                self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
            self.assertNotIn("control", self.state["uploads"])

    def test_retention_receipt_contains_only_required_normalized_fields(self):
        marker = "fake-private-extra-field"
        self.transport.upload_changes = lambda uploaded: uploaded["fileRetention"]["value"].update(diagnostic=marker)
        self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.assertNotIn(marker, json.dumps(self.saved))
        self.assertEqual(self.state["uploads"]["control"]["fileRetention"],
                         {"mode": None, "retainUntilTimestamp": None})

    def test_missing_or_expired_retention_response_stops_as_unknown(self):
        self.transport.upload_changes = lambda uploaded: uploaded["fileRetention"]["value"].pop("retainUntilTimestamp")
        with self.assertRaisesRegex(c.Stop, "uploaded_retention_mismatch"):
            self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.setUp()
        self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
        self.transport.now = self.now
        self.transport.retention = {"mode": "compliance", "period": {"duration": 1, "unit": "days"}}
        self.transport.upload_changes = lambda uploaded: uploaded["fileRetention"]["value"].update(
            retainUntilTimestamp=int((self.now - dt.timedelta(days=1)).timestamp() * 1000))
        with self.assertRaisesRegex(c.Stop, "uploaded_retention_duration_mismatch"):
            self.invoke("locked-upload", ui=self.ui(True), artifact=self.artifact)

    def test_exact_capabilities_reject_global_extra_missing_or_duplicate(self):
        for bad in (sorted(c.APPROVED_CAPABILITIES) + ["listAllBucketNames"],
                    sorted(c.APPROVED_CAPABILITIES) + ["writeKeys"],
                    sorted(c.APPROVED_CAPABILITIES)[:-1], sorted(c.APPROVED_CAPABILITIES) + ["writeFiles"]):
            with self.subTest(bad=bad):
                self.setUp()
                self.profile["capabilities"] = bad
                with self.assertRaises(c.Stop): self.invoke("authorize")
                self.assertEqual(self.transport.calls, [])

    def test_authorized_account_bucket_prefix_capabilities_or_origins_must_match(self):
        mutations = [lambda auth: auth.update(accountId="other-account"),
            lambda auth: auth["apiInfo"]["storageApi"]["allowed"].update(namePrefix=""),
            lambda auth: auth["apiInfo"]["storageApi"]["allowed"].update(buckets=None),
            lambda auth: auth["apiInfo"]["storageApi"]["allowed"]["capabilities"].append("listAllBucketNames"),
            lambda auth: auth["apiInfo"]["storageApi"].update(apiUrl="https://api999.backblazeb2.com")]
        for mutation in mutations:
            self.setUp()
            self.transport.auth_changes = mutation
            with self.assertRaises(c.Stop): self.invoke("authorize")
            self.assertEqual(len(self.transport.calls), 1)

    def test_expired_nonexpiring_or_long_lived_key_is_rejected(self):
        for expiry in (None, True, int(self.now.timestamp() * 1000) - 1,
                       int((self.now + dt.timedelta(days=2)).timestamp() * 1000)):
            self.transport.expiry = expiry
            with self.subTest(expiry=expiry), self.assertRaises(c.Stop): self.invoke("authorize")

    def test_each_upload_requires_recent_owner_ui_receipt_and_matching_native_policy(self):
        for field, value in (("operatorAuthorized", False), ("sseEnabled", False), ("bucketId", "other-id"),
                             ("observedAtUTC", c.stamp(self.now - dt.timedelta(hours=1))),
                             ("observedAtUTC", c.stamp(self.now + dt.timedelta(seconds=2))),
                             ("defaultRetention", {"mode": "compliance", "days": 1})):
            self.setUp()
            ui = self.ui()
            ui[field] = value
            with self.assertRaises(c.Stop): self.invoke("control-upload", ui=ui, artifact=self.artifact)
            self.assertFalse(self.transport.files)

    def test_locked_receipt_must_follow_completed_control_upload(self):
        self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.transport.retention = {"mode": "compliance", "period": {"duration": 1, "unit": "days"}}
        with self.assertRaisesRegex(c.Stop, "locked_ui_must_follow"):
            self.invoke("locked-upload", ui=self.ui(True), artifact=self.artifact)
        self.assertEqual(len(self.transport.files), 1)

    def test_filtered_bucket_lock_is_unknown_and_stops_without_upload(self):
        self.transport.lock_visible = False
        with self.assertRaisesRegex(c.Stop, "filtered_unknown"):
            self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.assertEqual(len(self.transport.calls), 2)

    def test_filtered_uploaded_retention_remains_uncertain_and_cannot_retry(self):
        self.transport.file_lock_visible = False
        with self.assertRaisesRegex(c.Stop, "filtered_unknown"):
            self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.assertEqual(self.saved[-1]["attemptedPhases"]["control-upload"]["status"], "started_outcome_unknown")
        calls = len(self.transport.calls)
        with self.assertRaisesRegex(c.Stop, "already_attempted"):
            self.invoke("control-upload", ui=self.ui(), artifact=self.artifact)
        self.assertEqual(len(self.transport.calls), calls)

    def test_same_ciphertext_cannot_hide_wrong_download_version(self):
        self.uploaded_pair()
        self.transport.wrong_download_identity = True
        with self.assertRaisesRegex(c.Stop, "download_verification_failed"):
            self.invoke("download-and-verify")
        self.assertFalse(self.state["verifiedDownloads"])

    def test_unauthorized_is_not_expected_retention_denial(self):
        self.ready_for_delete()
        self.transport.delete_error = "unauthorized"
        with self.assertRaisesRegex(c.Stop, "did_not_prove_expected_denial"):
            self.invoke("attempt-delete-locked")
        self.assertNotIn("lockedDeleteDeniedAndReadbackVerified", self.state)

    def test_delete_requires_verified_pair_and_successful_control_deletion(self):
        self.invoke("authorize")
        with self.assertRaisesRegex(c.Stop, "both_downloads_required"):
            self.invoke("delete-control")
        with self.assertRaisesRegex(c.Stop, "control_delete_and_locked_download"):
            self.invoke("attempt-delete-locked")

    def test_corrupt_private_receipt_cannot_delete_outside_fixed_canary_names(self):
        self.uploaded_pair()
        self.state["uploads"]["control"]["fileName"] = "production/data"
        calls = len(self.transport.calls)
        with self.assertRaisesRegex(c.Stop, "receipt_artifact_mismatch"):
            self.invoke("download-and-verify")
        self.assertEqual(len(self.transport.calls), calls)

    def test_artifact_identity_and_individual_bound_are_enforced(self):
        with self.assertRaisesRegex(c.Stop, "artifact_profile_mismatch"):
            self.invoke("control-upload", ui=self.ui(), artifact=b"different bytes")
        self.profile["artifactSizeBytes"] = c.ARTIFACT_MAX + 1
        with self.assertRaisesRegex(c.Stop, "artifact_size_invalid"):
            self.invoke("authorize")

    def test_maximum_pair_is_exactly_one_mib_not_an_unbounded_stream(self):
        self.artifact = b"x" * c.ARTIFACT_MAX
        self.profile.update(artifactSizeBytes=len(self.artifact), artifactSha256=c.sha256(self.artifact))
        self.uploaded_pair()
        self.assertEqual(sum(entry["sizeBytes"] for entry in self.state["uploads"].values()), 1048576)


class TransportAndFilesystemTests(unittest.TestCase):
    def test_upload_url_formats_and_adversarial_variants(self):
        root = "https://pod-000-1005-03.backblaze.com/b2api/v4/b2_upload_file"
        for valid in (root + "?cvt=opaque&bucket=expected", root + "/expected/opaque"):
            self.assertEqual(c.validate_upload_url(valid, "expected"), valid)
        for invalid in (root + "?cvt=x&bucket=other", root + "?cvt=x&bucket=expected&bucket=expected",
                        root + "?cvt=x&bucket=expected&redirect=evil", root + "/other/x", root + "/expected/../x",
                        root.replace("https:", "http:") + "/expected/x", root + "/expected/x#secret",
                        root.replace(".backblaze.com", ".backblaze.com.evil.invalid") + "/expected/x",
                        root.replace("https://", "https://user:secret@") + "/expected/x",
                        root.replace(".com/", ".com:443/") + "/expected/x"):
            with self.subTest(url=invalid), self.assertRaises(c.Stop): c.validate_upload_url(invalid, "expected")

    def test_metadata_json_rejects_duplicates_constants_and_large_input(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b"x" * (c.JSON_MAX + 1), b"\xff"):
            with self.subTest(raw=raw[:40]), self.assertRaises(c.Stop): c.decode(raw)

    def test_real_transport_has_no_proxy_or_redirect_and_rejects_nonallowlisted_target(self):
        sample = CanaryTests(); sample.setUp()
        with mock.patch("urllib.request.build_opener") as build:
            client = c.Transport(sample.profile)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], c.NoRedirect)
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid"))
        client.opener = mock.Mock()
        for method, url in (("GET", "http://api.backblazeb2.com/"),
                            ("POST", sample.profile["apiOrigin"] + "/b2api/v4/b2_update_bucket"),
                            ("GET", "https://127.0.0.1/")):
            with self.assertRaises(c.Stop): client.request(method, url, {})
        client.opener.open.assert_not_called()

    def test_network_errors_are_redacted_and_never_retried(self):
        sample = CanaryTests(); sample.setUp()
        client = c.Transport(sample.profile)
        client.opener = mock.Mock()
        client.opener.open.side_effect = TimeoutError("private-token-should-not-escape")
        with self.assertRaises(c.Stop) as caught: client.request("GET", c.BOOTSTRAP, {})
        self.assertNotIn("private-token", str(caught.exception))
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.opener.open.call_args.kwargs["timeout"], 15)

    def test_bound_reads_detect_overflow_and_http_redirect_is_not_success(self):
        sample = CanaryTests(); sample.setUp()
        for status, body in ((200, b"x" * 11), (302, b"{}")):
            client = c.Transport(sample.profile)
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.status, response.headers = status, {}
            response.read.return_value = body
            client.opener = mock.Mock()
            client.opener.open.return_value = response
            if len(body) > 10:
                with self.assertRaisesRegex(c.Stop, "response_size_limit"): client.request("GET", c.BOOTSTRAP, {}, limit=10)
            else:
                with self.assertRaisesRegex(c.Stop, "metadata_http_not_200"):
                    c.json_call(client, "GET", c.BOOTSTRAP, "fake-token")
            self.assertEqual(client.opener.open.call_count, 1)

    def test_private_files_receipt_lock_and_verified_ciphertext_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); os.chmod(base, 0o700)
            path = base / "receipt.json"
            c.save(path, {"private": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with c.receipt_lock(path):
                with self.assertRaisesRegex(c.Stop, "another_canary_invocation_active"):
                    with c.receipt_lock(path): pass
            c.store_ciphertext(base, "locked", b"synthetic ciphertext")
            self.assertEqual((base / "locked.gpg").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(c.Stop): c.store_ciphertext(base, "locked", b"changed")
            link = base / "link.json"; link.symlink_to(path)
            with self.assertRaises(c.Stop): c.private_read(link)
            os.chmod(path, 0o644)
            with self.assertRaises(c.Stop): c.private_read(path)


if __name__ == "__main__":
    unittest.main()
