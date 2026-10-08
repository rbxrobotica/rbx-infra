"""Safety and integrity contracts for the encrypted Plausible S3 archive."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1] / "plausible" / "s3_archive.py"
SPEC = importlib.util.spec_from_file_location("plausible_s3_archive", SOURCE)
archive = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = archive
SPEC.loader.exec_module(archive)

KEY = "plausible/backups/2026-10-05/20261005T120000Z_abc123.gpg"
PAYLOAD = b"test encrypted fixture, not a real backup"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
PRIVATE_ACL = b"""<AccessControlPolicy xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<AccessControlList><Grant><Grantee><ID>owner</ID></Grantee>
<Permission>FULL_CONTROL</Permission></Grant></AccessControlList></AccessControlPolicy>"""
VERSIONING = b"<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>"
DENY_POLICY = json.dumps({"Statement": [{"Effect": "Deny", "Principal": "*",
                                         "Action": "s3:DeleteObject"}]}).encode()


class Response(io.BytesIO):
    def __init__(self, data=b"", status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


def config(**overrides):
    return archive.Config(**{"endpoint": "https://eu2.contabostorage.com", "bucket": "rbx-data-lake",
                             "region": "eu-central-1", "access_key": "fixture-access",
                             "secret_key": "fixture-secret", **overrides})


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.client = archive.ArchiveClient(config())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.file = self.directory / "backup.gpg"
        self.file.write_bytes(PAYLOAD)

    def version_headers(self, **extra):
        return {"x-amz-version-id": "version-1", "Content-Length": str(len(PAYLOAD)), **extra}

    def test_credentials_must_be_present_in_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(archive.ArchiveError, "AWS_ACCESS_KEY_ID"):
                archive.Config.from_env()

    def test_config_repr_excludes_all_credentials(self):
        representation = repr(config(session_token="fixture-token"))
        for secret in ("fixture-access", "fixture-secret", "fixture-token"):
            self.assertNotIn(secret, representation)

    def test_endpoint_rejects_credentials_redirect_paths_and_cleartext(self):
        for endpoint in ("http://example.test", "https://user:pass@example.test",
                         "https://example.test/bucket", "https://example.test?next=evil",
                         "https://example.test#fragment", "https://example.test:8443"):
            with self.subTest(endpoint=endpoint), self.assertRaises(archive.ArchiveError):
                config(endpoint=endpoint)

    def test_prefix_validation_prevents_escape_and_url_injection(self):
        for key in ("other/backups/a.gpg", "plausible/backups/../secret.gpg",
                    "plausible/backups/%2e%2e/a.gpg", "plausible/backups/a.gpg?versionId=evil",
                    "plausible/backups//a.gpg", "plausible/backups/a.txt",
                    "plausible/backups/a\\b.gpg", "plausible/backups/a.gpg#x"):
            with self.subTest(key=key), self.assertRaises(archive.ArchiveError):
                archive.validate_key(key)
        self.assertEqual(archive.validate_key(KEY), KEY)

    def test_generated_keys_have_utc_timestamp_and_unique_identifier(self):
        first, second = archive.new_key(".gpg"), archive.new_key(".gpg")
        self.assertRegex(first, r"^plausible/backups/full/\d{4}-\d{2}-\d{2}/\d{8}T\d{6}Z_[0-9a-f]{32}\.gpg$")
        self.assertNotEqual(first, second)

    def test_artifact_kinds_are_fixed_and_legacy_keys_stay_readable(self):
        for kind in archive.ARTIFACT_KINDS:
            self.assertTrue(archive.new_key(".age", kind).startswith("plausible/backups/" + kind + "/"))
        for kind in ("../other", "full/a", "", "FULL"):
            with self.subTest(kind=kind), self.assertRaises(archive.ArchiveError):
                archive.new_key(".gpg", kind)
        self.assertEqual(archive.validate_key(KEY), KEY)

    def test_redirects_are_not_followed_and_error_is_redacted(self):
        redirect = archive.NoRedirect()
        request = urllib.request.Request("https://example.test")
        self.assertIsNone(redirect.redirect_request(request, None, 307, "moved", {},
                                                    "https://attacker.test/fixture-secret"))
        error = urllib.error.HTTPError("https://example.test/fixture-secret", 307,
                                       "fixture-secret", {}, io.BytesIO(b"fixture-secret"))
        self.client.opener = mock.Mock()
        self.client.opener.open.side_effect = error
        with self.assertRaises(archive.RequestError) as result:
            self.client._request("GET", key=KEY)
        self.assertEqual(result.exception.status, 307)
        self.assertNotIn("fixture-secret", str(result.exception))
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_private_versioned_bucket_passes_preflight(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(PRIVATE_ACL), Response(DENY_POLICY), Response(VERSIONING)]) as request:
            self.client.check_bucket()
        self.assertEqual([call.kwargs["query"] for call in request.call_args_list],
                         [{"acl": ""}, {"policy": ""}, {"versioning": ""}])

    def test_public_acl_is_rejected_before_upload(self):
        public_acl = PRIVATE_ACL.replace(b"<ID>owner</ID>",
                                         b"<URI>http://acs.amazonaws.com/groups/global/AllUsers</URI>")
        with mock.patch.object(self.client, "_request", return_value=Response(public_acl)) as request:
            with self.assertRaisesRegex(archive.ArchiveError, "private bucket"):
                self.client.upload(self.file)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET"])

    def test_public_bucket_policy_is_rejected_even_with_private_acl(self):
        for principal in ("*", {"AWS": "*"}, {"AWS": ["arn:aws:iam::*:root"]}):
            policy = json.dumps({"Statement": [{"Effect": "Allow", "Principal": principal}]}).encode()
            with self.subTest(principal=principal), mock.patch.object(self.client, "_request", side_effect=[
                    Response(PRIVATE_ACL), Response(policy)]):
                with self.assertRaisesRegex(archive.ArchiveError, "public access"):
                    self.client.check_bucket()

    def test_bucket_policy_not_readable_fails_closed(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(PRIVATE_ACL), archive.RequestError(403, "AccessDenied")]):
            with self.assertRaises(archive.RequestError):
                self.client.check_bucket()

    def test_bucket_without_policy_still_requires_versioning(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(PRIVATE_ACL), archive.RequestError(404, "NoSuchBucketPolicy"),
                Response(b"<VersioningConfiguration><Status>Suspended</Status></VersioningConfiguration>")]):
            with self.assertRaisesRegex(archive.ArchiveError, "Enabled"):
                self.client.check_bucket()

    def test_upload_receipt_requires_download_matching_exact_version(self):
        with mock.patch.object(self.client, "check_bucket"), mock.patch.object(archive, "new_key", return_value=KEY), \
                mock.patch.object(self.client, "_request", side_effect=[
                    Response(headers=self.version_headers()), Response(headers=self.version_headers()),
                    Response(PAYLOAD, headers=self.version_headers())]) as request:
            receipt = self.client.upload(self.file)
        self.assertEqual(receipt["sha256"], SHA)
        self.assertEqual(receipt["version_id"], "version-1")
        self.assertEqual(receipt["size_bytes"], len(PAYLOAD))
        self.assertFalse(receipt["recovered_after_ambiguous_put"])
        self.assertEqual([call.args[0] for call in request.call_args_list], ["PUT", "HEAD", "GET"])
        self.assertEqual(request.call_args_list[0].kwargs["headers"]["If-None-Match"], "*")
        self.assertEqual(request.call_args_list[2].kwargs["query"], {"versionId": "version-1"})

    def test_ambiguous_put_is_recovered_without_retrying_the_write(self):
        with mock.patch.object(self.client, "check_bucket"), \
                mock.patch.object(self.client, "_request", side_effect=[archive.RequestError(None),
                    Response(headers=self.version_headers()), Response(PAYLOAD, headers=self.version_headers())]) as request:
            receipt = self.client.upload(self.file)
        self.assertTrue(receipt["recovered_after_ambiguous_put"])
        self.assertEqual([call.args[0] for call in request.call_args_list].count("PUT"), 1)

    def test_conditional_conflict_never_overwrites_or_retries(self):
        with mock.patch.object(self.client, "check_bucket"), \
                mock.patch.object(self.client, "_request", side_effect=archive.RequestError(412, "PreconditionFailed")) as request:
            with self.assertRaises(archive.RequestError):
                self.client.upload(self.file)
        self.assertEqual(request.call_count, 1)

    def test_download_hash_mismatch_leaves_no_output_or_partial_file(self):
        destination = self.directory / "restore.gpg"
        wrong = bytes([PAYLOAD[0] ^ 1]) + PAYLOAD[1:]
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(headers=self.version_headers()), Response(wrong, headers=self.version_headers())]):
            with self.assertRaisesRegex(archive.ArchiveError, "SHA-256"):
                self.client.download(KEY, destination, SHA)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".plausible-download-*")), [])

    def test_download_rejects_wrong_version(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(headers=self.version_headers()),
                Response(PAYLOAD, headers=self.version_headers(**{"x-amz-version-id": "other-version"}))]):
            with self.assertRaisesRegex(archive.ArchiveError, "different object version"):
                self.client.download(KEY, self.directory / "restore.gpg", SHA)

    def test_download_rejects_stream_larger_than_head(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(headers=self.version_headers()), Response(PAYLOAD + b"more", headers=self.version_headers())]):
            with self.assertRaisesRegex(archive.ArchiveError, "declared size"):
                self.client.download(KEY, self.directory / "restore.gpg", SHA)

    def test_verified_download_is_private_and_does_not_replace_existing_file(self):
        destination = self.directory / "restore.gpg"
        with mock.patch.object(self.client, "_request", side_effect=[
                Response(headers=self.version_headers()), Response(PAYLOAD, headers=self.version_headers())]):
            receipt = self.client.download(KEY, destination, SHA)
        self.assertTrue(receipt["verified"])
        self.assertEqual(destination.read_bytes(), PAYLOAD)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with mock.patch.object(self.client, "head", return_value={"version_id": "version-1", "size_bytes": len(PAYLOAD)}):
            with self.assertRaisesRegex(archive.ArchiveError, "already exists"):
                self.client.download(KEY, destination, SHA)
        self.assertEqual(destination.read_bytes(), PAYLOAD)

    def test_maximum_file_size_is_checked_before_any_network_request(self):
        self.client = archive.ArchiveClient(config(max_bytes=1))
        with mock.patch.object(self.client, "_request") as request:
            with self.assertRaisesRegex(archive.ArchiveError, "size limit"):
                self.client.upload(self.file)
        request.assert_not_called()

    def test_read_retry_is_bounded_and_error_text_never_contains_credentials(self):
        self.client.opener = mock.Mock()
        self.client.opener.open.side_effect = urllib.error.URLError("fixture-access fixture-secret")
        with mock.patch.object(archive.time, "sleep"), self.assertRaises(archive.RequestError) as result:
            self.client._request("GET", key=KEY)
        self.assertEqual(self.client.opener.open.call_count, 3)
        self.assertEqual(str(result.exception), "S3 transport failed")

    def test_unrecognized_error_code_cannot_echo_provider_content(self):
        self.assertEqual(str(archive.RequestError(403, "SensitiveValue123")), "S3 returned HTTP 403")

    def test_xml_entity_expansion_is_rejected(self):
        with self.assertRaisesRegex(archive.ArchiveError, "Unsupported XML"):
            archive.parse_xml(b'<!DOCTYPE x [<!ENTITY a "sensitive">]><x>&a;</x>')

    def test_alternate_xml_encoding_cannot_bypass_entity_rejection(self):
        content = '<!DOCTYPE x [<!ENTITY a "sensitive">]><x>&a;</x>'
        for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32"):
            with self.subTest(encoding=encoding), self.assertRaises(archive.ArchiveError):
                archive.parse_xml(content.encode(encoding))

    def test_cli_failure_is_sanitized(self):
        output = io.StringIO()
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(archive.ArchiveClient, "upload", side_effect=OSError("fixture-secret")), \
                mock.patch.object(sys, "stderr", output):
            self.assertEqual(archive.main(["upload", str(self.file)]), 1)
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error"],
                         "Archive operation failed; inspect local file access and S3 connectivity")

    def test_upload_cli_keeps_default_full_and_accepts_explicit_weekly(self):
        for extra, expected in (([], "full"), (["--artifact-kind", "weekly"], "weekly")):
            with self.subTest(kind=expected), mock.patch.object(archive.Config, "from_env", return_value=config()), \
                    mock.patch.object(archive.ArchiveClient, "upload", return_value={}) as upload, \
                    mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(archive.main(["upload", str(self.file), *extra]), 0)
            upload.assert_called_once_with(self.file, expected)

    def test_cli_inaccessible_archive_is_sanitized_without_traceback(self):
        output = io.StringIO()
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(Path, "open", side_effect=PermissionError("fixture-secret")), \
                mock.patch.object(sys, "stderr", output):
            self.assertEqual(archive.main(["upload", str(self.file)]), 1)
        self.assertEqual(set(json.loads(output.getvalue())), {"error"})
        for forbidden in ("fixture-secret", "Traceback", "NameError"):
            self.assertNotIn(forbidden, output.getvalue())

    def test_cli_network_oserror_is_sanitized_without_traceback(self):
        output = io.StringIO()
        opener = mock.Mock()
        opener.open.side_effect = OSError("fixture-secret")
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(archive.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(archive.time, "sleep"), mock.patch.object(sys, "stderr", output):
            self.assertEqual(archive.main(["head", KEY]), 1)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(json.loads(output.getvalue()), {"error": "S3 transport failed"})

    def test_cli_http_exception_is_sanitized_without_traceback(self):
        output = io.StringIO()
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(archive.ArchiveClient, "head",
                                  side_effect=archive.http.client.HTTPException("fixture-secret")), \
                mock.patch.object(sys, "stderr", output):
            self.assertEqual(archive.main(["head", KEY]), 1)
        self.assertEqual(set(json.loads(output.getvalue())), {"error"})
        for forbidden in ("fixture-secret", "Traceback", "NameError"):
            self.assertNotIn(forbidden, output.getvalue())


class LatestArchiveTests(unittest.TestCase):
    def setUp(self):
        self.client = archive.ArchiveClient(config())
        self.now = archive.dt.datetime(2026, 10, 5, 12, 0, tzinfo=archive.dt.timezone.utc)
        patch = mock.patch.object(archive.dt, "datetime", wraps=archive.dt.datetime)
        self.clock = patch.start()
        self.addCleanup(patch.stop)
        self.clock.now.return_value = self.now

    def listing(self, day, objects=(), *, kind="full", truncated=False):
        root = ET.Element("ListBucketResult")
        ET.SubElement(root, "Name").text = "rbx-data-lake"
        ET.SubElement(root, "Prefix").text = f"plausible/backups/{kind}/{day}/"
        ET.SubElement(root, "KeyCount").text = str(len(objects))
        ET.SubElement(root, "IsTruncated").text = str(truncated).lower()
        for key, modified, size in objects:
            item = ET.SubElement(root, "Contents")
            for tag, value in (("Key", key), ("LastModified", modified), ("Size", str(size))):
                ET.SubElement(item, tag).text = value
        return Response(ET.tostring(root))

    def head_response(self, modified="Mon, 05 Oct 2026 11:00:00 GMT", **headers):
        return Response(headers={"Content-Length": str(len(PAYLOAD)), "x-amz-version-id": "version-1",
                                 "x-amz-meta-sha256": SHA, "Last-Modified": modified, **headers})

    def test_latest_full_uses_three_daily_lists_then_head_of_newest_object(self):
        first = "plausible/backups/full/2026-10-05/20261005T090000Z_first.gpg"
        newest = "plausible/backups/full/2026-10-05/20261005T110000Z_newest.gpg"
        old = "plausible/backups/full/2026-10-04/20261004T110000Z_old.gpg"
        with mock.patch.object(self.client, "_request", side_effect=[
                self.listing("2026-10-05", [(newest, "2026-10-05T11:00:00.000Z", len(PAYLOAD)),
                                          (first, "2026-10-05T09:00:00.000Z", len(PAYLOAD))]),
                self.listing("2026-10-04", [(old, "2026-10-04T11:00:00.000Z", len(PAYLOAD))]),
                self.listing("2026-10-03"), self.head_response()]) as request:
            result = self.client.latest()
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["key"], newest)
        self.assertEqual(result["version_id"], "version-1")
        self.assertEqual(result["sha256"], SHA)
        self.assertEqual(result["size_bytes"], len(PAYLOAD))
        self.assertEqual(result["age_seconds"], 3600)
        self.assertEqual(result["integrity"], "metadata_only")
        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET", "GET", "GET", "HEAD"])
        self.assertTrue(all(call.kwargs["max_attempts"] == 1 for call in request.call_args_list[:3]))
        self.assertEqual([call.kwargs["query"]["prefix"] for call in request.call_args_list[:3]],
                         [f"plausible/backups/full/2026-10-0{day}/" for day in (5, 4, 3)])

    def test_missing_is_structured_and_does_not_head_unrelated_artifacts(self):
        with mock.patch.object(self.client, "_request", side_effect=[
                self.listing("2026-10-05"), self.listing("2026-10-04"), self.listing("2026-10-03")]) as request:
            result = self.client.latest()
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["reason"], "no_objects_in_window")
        self.assertEqual(result["list_requests"], 3)
        self.assertNotIn("key", result)
        self.assertEqual(request.call_count, 3)

    def test_weekly_and_restore_proof_have_separate_listing_prefixes(self):
        for kind in ("weekly", "restore-proof"):
            with self.subTest(kind=kind), mock.patch.object(self.client, "_request", side_effect=[
                    self.listing("2026-10-05", kind=kind)]) as request:
                result = self.client.latest(kind, 1)
            self.assertEqual(result["status"], "missing")
            self.assertEqual(request.call_args.kwargs["query"]["prefix"],
                             f"plausible/backups/{kind}/2026-10-05/")

    def test_truncated_listing_is_unknown_never_missing(self):
        with mock.patch.object(self.client, "_request", return_value=self.listing("2026-10-05", truncated=True)) as request:
            result = self.client.latest()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "listing_truncated")
        self.assertEqual(request.call_count, 1)

    def test_more_than_1000_objects_is_unknown_even_when_truncation_flag_lies(self):
        item = ("plausible/backups/full/2026-10-05/a.gpg", "2026-10-05T11:00:00Z", 1)
        with mock.patch.object(self.client, "_request", return_value=self.listing("2026-10-05", [item] * 1001)):
            result = self.client.latest()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "listing_truncated")

    def test_list_network_failure_performs_one_http_attempt_and_reports_unknown(self):
        self.client.opener = mock.Mock()
        self.client.opener.open.side_effect = OSError("fixture-secret")
        result = self.client.latest()
        self.assertEqual(self.client.opener.open.call_count, 1)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["list_requests"], 1)
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_listing_cannot_cross_kind_or_use_legacy_unclassified_objects(self):
        for key in (KEY, "plausible/backups/weekly/2026-10-05/a.gpg"):
            with self.subTest(key=key), mock.patch.object(self.client, "_request", return_value=self.listing(
                    "2026-10-05", [(key, "2026-10-05T11:00:00Z", 1)])):
                result = self.client.latest()
            self.assertEqual(result["status"], "unknown")

    def test_missing_or_invalid_hash_metadata_cannot_be_reported_found(self):
        key = "plausible/backups/full/2026-10-05/a.gpg"
        with mock.patch.object(self.client, "_request", side_effect=[
                self.listing("2026-10-05", [(key, "2026-10-05T11:00:00Z", len(PAYLOAD))]),
                self.head_response(**{"x-amz-meta-sha256": ""})]):
            result = self.client.latest(lookback_days=1)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("sha256", result)

    def test_object_changed_after_list_is_unknown(self):
        key = "plausible/backups/full/2026-10-05/a.gpg"
        with mock.patch.object(self.client, "_request", side_effect=[
                self.listing("2026-10-05", [(key, "2026-10-05T11:00:00Z", len(PAYLOAD))]),
                self.head_response(modified="Mon, 05 Oct 2026 11:01:00 GMT")]):
            result = self.client.latest(lookback_days=1)
        self.assertEqual(result["status"], "unknown")

    def test_invalid_lookback_or_kind_makes_no_request(self):
        with mock.patch.object(self.client, "_request") as request:
            for days in (0, -1, 32, True, "3"):
                with self.subTest(days=days), self.assertRaises(archive.ArchiveError):
                    self.client.latest(lookback_days=days)
            with self.assertRaises(archive.ArchiveError):
                self.client.latest("../full", 3)
        request.assert_not_called()

    def test_latest_cli_reports_unknown_with_nonzero_exit(self):
        output = io.StringIO()
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(archive.ArchiveClient, "latest", return_value={"status": "unknown"}) as latest, \
                mock.patch.object(sys, "stdout", output):
            result = archive.main(["latest", "--artifact-kind", "full", "--lookback-days", "3"])
        self.assertEqual(result, 1)
        latest.assert_called_once_with("full", 3)
        self.assertEqual(json.loads(output.getvalue()), {"status": "unknown"})

    def test_latest_cli_preserves_structured_missing(self):
        output = io.StringIO()
        with mock.patch.object(archive.Config, "from_env", return_value=config()), \
                mock.patch.object(archive.ArchiveClient, "latest", return_value={"status": "missing"}), \
                mock.patch.object(sys, "stdout", output):
            self.assertEqual(archive.main(["latest"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"status": "missing"})


if __name__ == "__main__":
    unittest.main()
