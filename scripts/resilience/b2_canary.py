#!/usr/bin/env python3
"""Bounded operator-driven B2 canary. No credential loading and no retries."""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

JSON_MAX = 65536
ARTIFACT_MAX = 524288
BOOTSTRAP = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
PHASES = ("authorize", "control-upload", "locked-upload", "download-and-verify",
          "delete-control", "attempt-delete-locked")
MUTATING = frozenset(PHASES) - {"authorize", "download-and-verify"}
APPROVED_CAPABILITIES = frozenset({
    "bypassGovernance", "deleteFiles", "listBuckets", "listFiles", "readBucketEncryption",
    "readBucketLifecycleRules", "readBucketLogging", "readBucketNotifications", "readBucketReplications",
    "readBucketRetentions", "readBuckets", "readFileLegalHolds", "readFileRetentions", "readFiles", "shareFiles",
    "writeBucketEncryption", "writeBucketLifecycleRules", "writeBucketLogging", "writeBucketNotifications",
    "writeBucketReplications", "writeBucketRetentions", "writeBuckets", "writeFileLegalHolds",
    "writeFileRetentions", "writeFiles"})
PROFILE_FIELDS = {"schema", "accountId", "bucketId", "bucketName", "prefix", "keyId",
                  "keyCreatedAtUTC", "capabilities", "apiOrigin", "downloadOrigin",
                  "artifactSha256", "artifactSizeBytes", "syntheticOnly"}


class Stop(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Stop(code)


def stamp(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_stamp(value):
    require(type(value) is str and value.endswith("Z"), "invalid_timestamp")
    try:
        return dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise Stop("invalid_timestamp") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def decode(data):
    require(type(data) is bytes and 0 < len(data) <= JSON_MAX, "json_size_limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Stop("invalid_json_constant")))
    except (UnicodeError, ValueError, RecursionError):
        raise Stop("invalid_json") from None
    require(type(value) is dict, "json_object_required")
    return value


def private_read(path, limit=JSON_MAX):
    path = Path(path)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_uid == os.getuid(), "private_regular_0600_required")
            data = stream.read(limit + 1)
    except OSError:
        raise Stop("private_file_unavailable") from None
    require(0 < len(data) <= limit, "private_file_size_limit")
    return data


def save(path, value):
    path = Path(path)
    info = path.parent.stat()
    require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid(), "private_parent_0700_required")
    require(not path.is_symlink(), "receipt_symlink_rejected")
    if path.exists():
        private_read(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def receipt_lock(path):
    parent = Path(path).parent.stat()
    require(stat.S_IMODE(parent.st_mode) == 0o700 and parent.st_uid == os.getuid(), "private_parent_0700_required")
    try:
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_uid == os.getuid(), "private_lock_required")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise Stop("another_canary_invocation_active") from None
            yield
    except OSError:
        raise Stop("private_lock_unavailable") from None


def identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) is not None


def token(value):
    require(type(value) is str and 0 < len(value) <= 8192
            and all(32 < ord(char) < 127 for char in value), "invalid_token_shape")
    return value


def profile_check(profile, now):
    require(type(profile) is dict and set(profile) == PROFILE_FIELDS, "profile_fields_mismatch")
    require(profile["schema"] == "rbx.b2.canary-profile.v1" and profile["syntheticOnly"] is True,
            "synthetic_profile_required")
    require(all(identifier(profile[name]) for name in ("accountId", "bucketId", "keyId")), "profile_identity_invalid")
    require(type(profile["bucketName"]) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{4,61}[a-z0-9]", profile["bucketName"]), "bucket_name_invalid")
    require(type(profile["prefix"]) is str
            and re.fullmatch(r"rbx-resilience-canary/[a-z0-9-]{1,64}/", profile["prefix"]), "prefix_invalid")
    for field, pattern in (("apiOrigin", r"https://api[0-9]{3}\.backblazeb2\.com"),
                           ("downloadOrigin", r"https://f[0-9]{3}\.backblazeb2\.com")):
        require(type(profile[field]) is str and re.fullmatch(pattern, profile[field]), "profile_origin_invalid")
    caps = profile["capabilities"]
    require(type(caps) is list and all(type(cap) is str for cap in caps)
            and len(caps) == len(set(caps)) and set(caps) == APPROVED_CAPABILITIES, "profile_capabilities_invalid")
    require(type(profile["artifactSizeBytes"]) is int and 0 < profile["artifactSizeBytes"] <= ARTIFACT_MAX,
            "artifact_size_invalid")
    require(type(profile["artifactSha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", profile["artifactSha256"]),
            "artifact_hash_invalid")
    created = parse_stamp(profile["keyCreatedAtUTC"])
    require(dt.timedelta(0) <= now - created < dt.timedelta(days=1), "key_creation_time_invalid")


def validate_upload_url(url, bucket_id):
    require(type(url) is str and len(url) <= 4096, "upload_url_invalid")
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.netloc == parsed.hostname
            and re.fullmatch(r"pod-[0-9]{3}-[0-9]{4}-[0-9]{2}\.backblaze\.com", parsed.netloc or "")
            and not parsed.fragment, "upload_origin_rejected")
    root = "/b2api/v4/b2_upload_file"
    opaque = r"[A-Za-z0-9_-]{1,1024}"
    if parsed.path == root:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        require(len(pairs) == 2 and len({key for key, _ in pairs}) == 2, "upload_query_rejected")
        query = dict(pairs)
        require(set(query) == {"bucket", "cvt"} and query["bucket"] == bucket_id
                and re.fullmatch(opaque, query["cvt"]), "upload_bucket_rejected")
    else:
        require(not parsed.query and re.fullmatch(re.escape(root + "/" + bucket_id + "/") + opaque, parsed.path),
                "upload_path_rejected")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    """No proxy inheritance, redirects, retries, credential files or debug logs."""
    def __init__(self, profile):
        self.profile = profile
        self.calls = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, url, headers, data=None, limit=JSON_MAX):
        self.calls += 1
        require(self.calls <= 8, "phase_call_budget_exceeded")
        allowed = url == BOOTSTRAP and method == "GET"
        for endpoint in ("b2_list_buckets", "b2_get_upload_url", "b2_delete_file_version"):
            allowed |= method == "POST" and url == self.profile["apiOrigin"] + "/b2api/v4/" + endpoint
        parsed = urllib.parse.urlsplit(url)
        if method == "GET" and url.startswith(self.profile["downloadOrigin"] + "/b2api/v4/b2_download_file_by_id?"):
            pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            allowed |= len(pairs) == 1 and pairs[0][0] == "fileId" and identifier(pairs[0][1])
        if method == "POST" and parsed.hostname and parsed.hostname.endswith(".backblaze.com"):
            validate_upload_url(url, self.profile["bucketId"])
            allowed = True
        require(allowed and type(limit) is int and 0 < limit <= ARTIFACT_MAX, "network_target_rejected")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(limit + 1)
                require(len(body) <= limit, "response_size_limit")
                return response.status, dict(response.headers), body
        except Stop:
            raise
        except Exception:
            raise Stop("network_request_failed_no_retry") from None


def json_call(transport, method, url, auth, body=None):
    headers = {"Authorization": auth}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = canonical(body)
    status, _, raw = transport.request(method, url, headers, data, JSON_MAX)
    require(status == 200, "metadata_http_not_200")
    return decode(raw)


def authorize(profile, credentials, transport, now):
    require(type(credentials) is dict and set(credentials) == {"keyId", "applicationKey"}, "credential_fields_mismatch")
    require(credentials["keyId"] == profile["keyId"], "key_id_mismatch")
    secret = token(credentials["applicationKey"])
    basic = "Basic " + base64.b64encode((profile["keyId"] + ":" + secret).encode()).decode()
    result = json_call(transport, "GET", BOOTSTRAP, basic)
    try:
        storage = result["apiInfo"]["storageApi"]
        scope = storage["allowed"]
        caps = scope["capabilities"]
        require(result["accountId"] == profile["accountId"]
                and scope["buckets"] == [{"id": profile["bucketId"], "name": profile["bucketName"]}]
                and scope["namePrefix"] == profile["prefix"]
                and type(caps) is list and sorted(caps) == sorted(profile["capabilities"])
                and storage["apiUrl"] == profile["apiOrigin"]
                and storage["downloadUrl"] == profile["downloadOrigin"], "authorization_scope_mismatch")
        expires = result["applicationKeyExpirationTimestamp"]
        require(type(expires) is int and now.timestamp() * 1000 < expires
                <= (parse_stamp(profile["keyCreatedAtUTC"]) + dt.timedelta(days=1)).timestamp() * 1000,
                "key_expiration_mismatch")
        return token(result["authorizationToken"])
    except (KeyError, TypeError, ValueError):
        raise Stop("authorization_shape_invalid") from None


def bucket_metadata(profile, transport, auth):
    result = json_call(transport, "POST", profile["apiOrigin"] + "/b2api/v4/b2_list_buckets", auth,
                       {"accountId": profile["accountId"], "bucketId": profile["bucketId"]})
    try:
        require(type(result["buckets"]) is list and len(result["buckets"]) == 1, "bucket_cardinality_mismatch")
        bucket = result["buckets"][0]
        require(bucket["accountId"] == profile["accountId"] and bucket["bucketId"] == profile["bucketId"]
                and bucket["bucketName"] == profile["bucketName"] and bucket["bucketType"] == "allPrivate"
                and bucket["lifecycleRules"] == [], "bucket_contract_mismatch")
        encryption = bucket["defaultServerSideEncryption"]
        require(encryption["isClientAuthorizedToRead"] is True
                and encryption["value"] == {"mode": "SSE-B2", "algorithm": "AES256"}, "sse_metadata_mismatch")
        lock = bucket["fileLockConfiguration"]
        require(type(lock) is dict and type(lock["isClientAuthorizedToRead"]) is bool, "lock_metadata_invalid")
        require(lock["isClientAuthorizedToRead"] is True, "lock_metadata_filtered_unknown")
        require(lock["value"]["isFileLockEnabled"] is True, "object_lock_not_enabled")
        retention = lock["value"]["defaultRetention"]
        require(retention in ({"mode": None, "period": None},
                {"mode": "compliance", "period": {"duration": 1, "unit": "days"}}), "unexpected_bucket_retention")
        return retention
    except (KeyError, TypeError, IndexError):
        raise Stop("bucket_metadata_invalid") from None


def ui_gate(profile, ui, phase, state, now, native_retention):
    try:
        require(ui["schema"] == "rbx.b2.canary-ui.v1" and ui["operatorAuthorized"] is True,
                "authorized_operator_ui_receipt_required")
        require(all(ui[key] == profile[key] for key in ("accountId", "bucketId", "bucketName", "prefix")),
                "ui_identity_mismatch")
        require(all(ui[key] is True for key in ("allPrivate", "keepAllVersions", "sseEnabled", "objectLockEnabled")),
                "ui_bucket_configuration_mismatch")
        observed = parse_stamp(ui["observedAtUTC"])
        require(dt.timedelta(0) <= now - observed <= dt.timedelta(minutes=15), "ui_receipt_not_fresh")
        expected = {"mode": None, "days": None} if phase == "control-upload" else {"mode": "compliance", "days": 1}
        require(ui["defaultRetention"] == expected, "ui_retention_mismatch")
        if phase == "locked-upload":
            require("control" in state["uploads"], "control_upload_required")
            require(observed > parse_stamp(state["uploads"]["control"]["receivedAtUTC"]),
                    "locked_ui_must_follow_control_upload")
        if native_retention is not None:
            native_expected = {"mode": None, "period": None} if phase == "control-upload" else {
                "mode": "compliance", "period": {"duration": 1, "unit": "days"}}
            require(native_retention == native_expected, "native_ui_retention_mismatch")
        return {"observedAtUTC": ui["observedAtUTC"], "defaultRetention": expected,
                "nativeLockMetadataVisible": native_retention is not None}
    except (KeyError, TypeError):
        raise Stop("ui_receipt_invalid") from None


def download(profile, transport, auth, entry):
    url = profile["downloadOrigin"] + "/b2api/v4/b2_download_file_by_id?" + urllib.parse.urlencode({"fileId": entry["fileId"]})
    status, headers, body = transport.request("GET", url, {"Authorization": auth}, limit=entry["sizeBytes"])
    normalized = {key.lower(): value for key, value in headers.items()}
    require(status == 200 and normalized.get("content-length") == str(entry["sizeBytes"])
            and normalized.get("x-bz-file-id") == entry["fileId"]
            and urllib.parse.unquote(normalized.get("x-bz-file-name", "")) == entry["fileName"]
            and normalized.get("x-bz-content-sha1") == entry["sha1"]
            and len(body) == entry["sizeBytes"] and sha256(body) == entry["sha256"], "download_verification_failed")
    return body


def store_ciphertext(directory, alias, body):
    require(alias in {"control", "locked", "locked-after-denial"}, "download_alias_invalid")
    directory = Path(directory)
    info = directory.stat()
    require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid(), "private_download_directory_required")
    target = directory / (alias + ".gpg")
    require(not target.is_symlink(), "download_symlink_rejected")
    if target.exists():
        require(private_read(target, ARTIFACT_MAX) == body, "existing_download_mismatch")
        return
    with target.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def state_check(profile, state):
    require(type(state.get("uploads")) is dict and set(state["uploads"]) <= {"control", "locked"},
            "receipt_uploads_invalid")
    for kind, entry in state["uploads"].items():
        require(type(entry) is dict and identifier(entry.get("fileId"))
                and entry.get("fileName") == profile["prefix"] + kind + ".gpg"
                and type(entry.get("sizeBytes")) is int and entry["sizeBytes"] == profile["artifactSizeBytes"]
                and entry.get("sha256") == profile["artifactSha256"]
                and type(entry.get("sha1")) is str and re.fullmatch(r"[0-9a-f]{40}", entry["sha1"]),
                "receipt_artifact_mismatch")
        parse_stamp(entry.get("receivedAtUTC"))
    require(type(state.get("verifiedDownloads")) is dict
            and set(state["verifiedDownloads"]) <= {"control", "locked"}
            and all(type(value) is bool for value in state["verifiedDownloads"].values())
            and type(state.get("controlDeleted")) is bool
            and type(state.get("attemptedPhases")) is dict
            and set(state["attemptedPhases"]) <= MUTATING, "receipt_state_invalid")


def run(phase, profile, credentials, state, persist, transport, artifact=None, ui=None, now=None, downloaded=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    require(phase in PHASES, "unknown_phase")
    profile_check(profile, now)
    fingerprint = sha256(canonical(profile))
    if not state:
        state.update({"schema": "rbx.b2.canary-receipt.v1", "profileSha256": fingerprint,
                      "uploads": {}, "attemptedPhases": {}, "verifiedDownloads": {}, "controlDeleted": False})
    require(state.get("schema") == "rbx.b2.canary-receipt.v1" and state.get("profileSha256") == fingerprint,
            "receipt_profile_mismatch")
    state_check(profile, state)
    require(phase not in state["attemptedPhases"] or phase not in MUTATING, "write_phase_already_attempted")
    auth = authorize(profile, credentials, transport, now)
    retention = bucket_metadata(profile, transport, auth)
    observation = None
    if phase.endswith("-upload"):
        require(type(artifact) is bytes and len(artifact) == profile["artifactSizeBytes"]
                and sha256(artifact) == profile["artifactSha256"], "artifact_profile_mismatch")
        observation = ui_gate(profile, ui, phase, state, now, retention)
        require(sum(item["sizeBytes"] for item in state["uploads"].values()) + len(artifact) <= 1048576,
                "total_upload_budget_exceeded")
    if phase == "delete-control":
        require(all(state["verifiedDownloads"].get(kind) is True for kind in ("control", "locked")),
                "both_downloads_required_before_delete")
    if phase == "attempt-delete-locked":
        require(state.get("controlDeleted") is True and state["verifiedDownloads"].get("locked") is True,
                "control_delete_and_locked_download_required")
    if phase in MUTATING:
        state["attemptedPhases"][phase] = {"startedAtUTC": stamp(now), "status": "started_outcome_unknown"}
        persist(state)  # Durable intent before any upload/delete. Never silently retry this phase.
    if phase.endswith("-upload"):
        kind = "control" if phase == "control-upload" else "locked"
        require(kind not in state["uploads"], "duplicate_upload_rejected")
        file_name = profile["prefix"] + kind + ".gpg"
        info = json_call(transport, "POST", profile["apiOrigin"] + "/b2api/v4/b2_get_upload_url", auth,
                         {"bucketId": profile["bucketId"]})
        require(info.get("bucketId") == profile["bucketId"], "upload_url_bucket_mismatch")
        url = validate_upload_url(info.get("uploadUrl"), profile["bucketId"])
        upload_auth = token(info.get("authorizationToken"))
        digest = hashlib.sha1(artifact).hexdigest()
        status, _, raw = transport.request("POST", url, {"Authorization": upload_auth,
            "X-Bz-File-Name": urllib.parse.quote(file_name, safe=""), "Content-Type": "application/octet-stream",
            "X-Bz-Content-Sha1": digest, "Content-Length": str(len(artifact))}, artifact, JSON_MAX)
        require(status == 200, "upload_http_not_200_outcome_unknown")
        uploaded = decode(raw)
        require(uploaded.get("accountId") == profile["accountId"] and uploaded.get("bucketId") == profile["bucketId"]
                and uploaded.get("fileName") == file_name and identifier(uploaded.get("fileId"))
                and type(uploaded.get("contentLength")) is int and uploaded["contentLength"] == len(artifact)
                and uploaded.get("contentSha1") == digest and uploaded.get("action") == "upload",
                "upload_response_mismatch_outcome_unknown")
        require(type(uploaded.get("uploadTimestamp")) is int
                and abs(uploaded["uploadTimestamp"] - now.timestamp() * 1000) <= 180000,
                "upload_timestamp_not_recent")
        file_retention = uploaded.get("fileRetention")
        visible = type(file_retention) is dict and file_retention.get("isClientAuthorizedToRead") is True
        require(visible, "uploaded_retention_filtered_unknown")
        value = file_retention.get("value")
        require(type(value) is dict and {"mode", "retainUntilTimestamp"} <= set(value)
                and value["mode"] == (None if kind == "control" else "compliance"),
                "uploaded_retention_mismatch_outcome_unknown")
        if kind == "control":
            require(value.get("retainUntilTimestamp") is None, "control_unexpected_retention")
        else:
            until = value.get("retainUntilTimestamp")
            require(type(until) is int and until > now.timestamp() * 1000
                    and abs(until - uploaded["uploadTimestamp"] - 86400000) <= 120000,
                    "uploaded_retention_duration_mismatch")
        hold = uploaded.get("legalHold")
        hold_readable = type(hold) is dict and hold.get("isClientAuthorizedToRead") is True
        hold_value = hold.get("value") if hold_readable else "unknown"
        if hold_value not in (None, "on", "off"):
            hold_value = "unknown"
        state["uploads"][kind] = {"fileId": uploaded["fileId"], "fileName": file_name,
            "sizeBytes": len(artifact), "sha256": sha256(artifact), "sha1": digest,
            "uploadTimestamp": uploaded["uploadTimestamp"], "receivedAtUTC": stamp(),
            "fileRetentionMetadataVisible": visible,
            "fileRetention": {"mode": value["mode"], "retainUntilTimestamp": value["retainUntilTimestamp"]},
            "legalHoldObservation": {"nativeReadable": hold_readable, "value": hold_value},
            "uiObservation": observation}
    elif phase == "download-and-verify":
        require(set(state["uploads"]) == {"control", "locked"}, "both_uploads_required")
        for kind in ("control", "locked"):
            body = download(profile, transport, auth, state["uploads"][kind])
            if downloaded:
                downloaded(kind, body)
            state["verifiedDownloads"][kind] = True
    elif phase in {"delete-control", "attempt-delete-locked"}:
        kind = "control" if phase == "delete-control" else "locked"
        require(kind in state["uploads"], "exact_uploaded_version_required")
        entry = state["uploads"][kind]
        status, _, raw = transport.request("POST", profile["apiOrigin"] + "/b2api/v4/b2_delete_file_version",
            {"Authorization": auth, "Content-Type": "application/json"},
            canonical({"fileId": entry["fileId"], "fileName": entry["fileName"]}), JSON_MAX)
        result = decode(raw)
        if kind == "control":
            require(status == 200 and result.get("fileId") == entry["fileId"]
                    and result.get("fileName") == entry["fileName"], "control_delete_failed_or_unknown")
            state["controlDeleted"] = True
        else:
            require(status == 401 and result.get("status") == 401 and result.get("code") == "access_denied",
                    "locked_delete_did_not_prove_expected_denial")
            body = download(profile, transport, auth, entry)
            if downloaded:
                downloaded("locked-after-denial", body)
            state["lockedDeleteDeniedAndReadbackVerified"] = True
    state["lastPhase"] = phase
    state["lastObservedAtUTC"] = stamp()
    if phase in MUTATING:
        state["attemptedPhases"][phase]["status"] = "completed"
    persist(state)
    return {"phase": phase, "phase_completed": True, "synthetic_only": True,
            "upload_count": len(state["uploads"]), "uploaded_bytes": sum(x["sizeBytes"] for x in state["uploads"].values()),
            "download_count_verified": sum(value is True for value in state["verifiedDownloads"].values()),
            "control_deleted": state["controlDeleted"],
            "locked_delete_denied_and_readback_verified": state.get("lockedDeleteDeniedAndReadbackVerified", False),
            "native_bucket_lock_metadata_visible": retention is not None,
            "independent_provenance_established": False, "production_backup_qualified": False,
            "H1_qualified": False, "application_restore_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--artifact")
    parser.add_argument("--ui-receipt")
    parser.add_argument("--downloads-dir")
    args = parser.parse_args()
    def timed_out(signum, frame):
        raise Stop("phase_wall_timeout_no_retry")
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(175)
    try:
        require(not sys.stdin.isatty(), "credential_stdin_pipe_required")
        profile = decode(private_read(args.profile))
        state_path = Path(args.receipt)
        with receipt_lock(state_path):
            require(not state_path.is_symlink(), "receipt_symlink_rejected")
            state = decode(private_read(state_path)) if state_path.exists() else {}
            artifact = private_read(args.artifact, ARTIFACT_MAX) if args.artifact else None
            ui = decode(private_read(args.ui_receipt)) if args.ui_receipt else None
            credentials = decode(sys.stdin.buffer.read(JSON_MAX + 1))
            require(args.phase != "download-and-verify" or args.downloads_dir is not None,
                    "private_download_directory_required")
            result = run(args.phase, profile, credentials, state, lambda item: save(state_path, item),
                         Transport(profile), artifact, ui,
                         downloaded=(lambda alias, body: store_ciphertext(args.downloads_dir, alias, body))
                         if args.downloads_dir else None)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Stop as error:
        print(json.dumps({"phase_completed": False, "error": str(error), "no_retry_performed": True}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"phase_completed": False, "error": "unexpected_failure_no_retry"}), file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
