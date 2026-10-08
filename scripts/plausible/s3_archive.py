#!/usr/bin/env python3
"""Archive already encrypted backups in private, versioned S3 storage.

This helper does not encrypt files or claim Object Lock protection. Credentials
are read only from AWS_* environment variables. It never deletes objects. Each
upload gets a new key and uses If-None-Match; success requires downloading the
stored version and comparing its SHA-256 with the local encrypted file.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.utils
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

PREFIX = "plausible/backups/"
ARTIFACT_KINDS = ("full", "weekly", "restore-proof")
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
METADATA_MAX_BYTES = 64 * 1024
LIST_MAX_BYTES = 2 * 1024 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SAFE_ERROR_CODES = frozenset({
    "AccessDenied", "NoSuchBucketPolicy", "NoSuchKey", "NoSuchVersion", "NoSuchBucket",
    "PreconditionFailed", "ConditionalRequestConflict", "InvalidAccessKeyId",
    "SignatureDoesNotMatch", "RequestTimeTooSkewed", "RequestTimeout", "SlowDown",
    "InternalError", "ServiceUnavailable", "InvalidRequest", "NotImplemented",
})


class ArchiveError(Exception):
    """A safe, credential-free error for callers and operational logs."""


class RequestError(ArchiveError):
    def __init__(self, status: int | None, code: str = ""):
        self.status = status
        # Do not echo arbitrary provider response content, even in a Code field.
        self.code = code if code in SAFE_ERROR_CODES else ""
        message = "S3 transport failed" if status is None else f"S3 returned HTTP {status}"
        super().__init__(message + (f" ({self.code})" if self.code else ""))


@dataclasses.dataclass(frozen=True)
class Config:
    endpoint: str
    bucket: str
    region: str
    access_key: str = dataclasses.field(repr=False)
    secret_key: str = dataclasses.field(repr=False)
    session_token: str = dataclasses.field(default="", repr=False)
    timeout_seconds: int = 30
    transfer_seconds: int = 300
    max_bytes: int = DEFAULT_MAX_BYTES
    scope: str = "plausible"

    def __post_init__(self):
        scope_prefix(self.scope)
        url = urllib.parse.urlsplit(self.endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")
                or not re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", url.netloc)):
            raise ArchiveError("S3 endpoint must be an HTTPS origin without credentials or a path")
        if (not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket)
                or ".." in self.bucket or re.fullmatch(r"[0-9.]+", self.bucket)):
            raise ArchiveError("Invalid S3 bucket name")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", self.region):
            raise ArchiveError("Invalid AWS region")
        if not self.access_key or not self.secret_key:
            raise ArchiveError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required")
        if any("\n" in value or "\r" in value for value in
               (self.access_key, self.secret_key, self.session_token)):
            raise ArchiveError("Invalid credential encoding")
        if not 1 <= self.timeout_seconds <= 60 or not 1 <= self.transfer_seconds <= 3600:
            raise ArchiveError("Timeout exceeds the supported bounds")
        if not 1 <= self.max_bytes <= 16 * 1024 * 1024 * 1024:
            raise ArchiveError("Archive size limit must be between 1 byte and 16 GiB")

    @classmethod
    def from_env(cls, *, max_bytes: int = DEFAULT_MAX_BYTES, scope: str = "plausible") -> Config:
        return cls(
            endpoint=os.environ.get("S3_ARCHIVE_ENDPOINT", "https://eu2.contabostorage.com"),
            bucket=os.environ.get("S3_ARCHIVE_BUCKET", "rbx-data-lake"),
            region=os.environ.get("AWS_REGION", "eu-central-1"),
            access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            session_token=os.environ.get("AWS_SESSION_TOKEN", ""),
            max_bytes=max_bytes,
            scope=scope,
        )


def scope_prefix(scope: str) -> str:
    # Explicit callers select the application; an environment variable cannot
    # redirect the existing Plausible CLI into another backup collection.
    if scope not in ("plausible", "comms"):
        raise ArchiveError("Unsupported backup scope")
    return f"{scope}/backups/"


def validate_key(key: str, *, scope: str = "plausible") -> str:
    prefix = scope_prefix(scope)
    if (not key.startswith(prefix) or len(key) > 512
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", key)
            or any(part in ("", ".", "..") for part in key.split("/"))
            or not key.endswith((".gpg", ".age"))):
        raise ArchiveError(f"Object key must identify an encrypted file below {prefix}")
    return key


def validate_version(version_id: str | None) -> dict[str, str]:
    if version_id is None:
        return {}
    if (not version_id or version_id == "null" or len(version_id) > 1024
            or any(ord(char) < 33 or ord(char) > 126 for char in version_id)):
        raise ArchiveError("A valid S3 object version is required")
    return {"versionId": version_id}


def artifact_prefix(artifact_kind: str, *, scope: str = "plausible") -> str:
    if artifact_kind not in ARTIFACT_KINDS:
        raise ArchiveError("Artifact kind must be full, weekly, or restore-proof")
    return scope_prefix(scope) + artifact_kind + "/"


def new_key(suffix: str, artifact_kind: str = "full", *, scope: str = "plausible") -> str:
    if suffix not in (".gpg", ".age"):
        raise ArchiveError("Only already encrypted .gpg or .age files may be uploaded")
    now = dt.datetime.now(dt.timezone.utc)
    return validate_key(f"{artifact_prefix(artifact_kind, scope=scope)}{now:%Y-%m-%d}/{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex}{suffix}", scope=scope)


def parse_timestamp(value: str, *, http_date: bool = False) -> dt.datetime:
    try:
        parsed = (email.utils.parsedate_to_datetime(value) if http_date
                  else dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ArchiveError("S3 did not return a valid UTC timestamp") from None


def parse_xml(body: bytes) -> ET.Element:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ArchiveError("S3 XML metadata must use UTF-8") from None
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ArchiveError("Unsupported XML metadata")
    try:
        # Callers bound input bytes; UTF-8/NUL/DTD/entity gates above prevent
        # alternate-encoding bypasses and external or expanding entities.
        root = ET.fromstring(text)  # nosec B314
    except ET.ParseError:
        raise ArchiveError("Invalid S3 XML metadata") from None
    for element in root.iter():
        element.tag = element.tag.split("}")[-1]
    return root


def public_principal(value) -> bool:
    if isinstance(value, str):
        return "*" in value or "?" in value
    if isinstance(value, list):
        return any(public_principal(item) for item in value)
    if isinstance(value, dict):
        return any(public_principal(item) for item in value.values())
    return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward signed requests or credentials to another URL.
        return None


class TimedReader:
    def __init__(self, stream, deadline: float):
        self.stream = stream
        self.deadline = deadline

    def read(self, size: int = -1):
        if time.monotonic() > self.deadline:
            raise ArchiveError("Archive transfer exceeded its time limit")
        return self.stream.read(size)


class ArchiveClient:
    def __init__(self, config: Config):
        self.config = config
        self.opener = urllib.request.build_opener(NoRedirect())

    def _signed_request(self, method, key, query, headers, payload_sha256, data):
        now = dt.datetime.now(dt.timezone.utc)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/" + self.config.bucket + ("/" + validate_key(key, scope=self.config.scope) if key else "")
        path = urllib.parse.quote(path, safe="/-_.~")
        query_string = urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
        signed = {name.lower(): str(value).strip() for name, value in headers.items()}
        signed.update({"host": urllib.parse.urlsplit(self.config.endpoint).netloc,
                       "x-amz-date": stamp, "x-amz-content-sha256": payload_sha256})
        if self.config.session_token:
            signed["x-amz-security-token"] = self.config.session_token
        names = ";".join(sorted(signed))
        canonical_headers = "".join(name + ":" + signed[name] + "\n" for name in sorted(signed))
        canonical = "\n".join((method, path, query_string, canonical_headers, names, payload_sha256))
        scope = f"{day}/{self.config.region}/s3/aws4_request"
        to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
        signing_key = ("AWS4" + self.config.secret_key).encode()
        for part in (day, self.config.region, "s3", "aws4_request"):
            signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        signed["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.config.access_key}/{scope}, "
            f"SignedHeaders={names}, Signature={signature}"
        )
        url = self.config.endpoint.rstrip("/") + path + ("?" + query_string if query_string else "")
        return urllib.request.Request(url, headers=signed, data=data, method=method)

    def _request(self, method, *, key=None, query=None, headers=None,
                 payload_sha256=EMPTY_SHA256, data=None, max_attempts=None):
        # PUT is deliberately never retried. GET/HEAD retry only transient failures.
        attempts = (3 if method in ("GET", "HEAD") else 1) if max_attempts is None else max_attempts
        if not 1 <= attempts <= 3 or (method == "PUT" and attempts != 1):
            raise ArchiveError("Invalid request retry bound")
        for attempt in range(attempts):
            request = self._signed_request(method, key, query or {}, headers or {}, payload_sha256, data)
            try:
                response = self.opener.open(request, timeout=self.config.timeout_seconds)
                if not 200 <= response.status < 300:
                    response.close()
                    raise RequestError(response.status)
                return response
            except urllib.error.HTTPError as exc:
                try:
                    code = parse_xml(exc.read(METADATA_MAX_BYTES)).findtext("Code", "")
                except (ArchiveError, OSError):
                    code = ""
                finally:
                    exc.close()
                error = RequestError(exc.code, code)
            except (urllib.error.URLError, OSError, socket.timeout, http.client.HTTPException):
                error = RequestError(None)
            if attempt + 1 == attempts or (error.status is not None and error.status not in (408, 429, 500, 502, 503, 504)):
                raise error from None
            time.sleep(0.25 * (2 ** attempt))
        raise ArchiveError("S3 request did not complete")

    @staticmethod
    def _small(response, limit=METADATA_MAX_BYTES):
        with response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ArchiveError("S3 metadata exceeds the size limit")
        return data

    def check_bucket(self):
        root = parse_xml(self._small(self._request("GET", query={"acl": ""})))
        if root.tag != "AccessControlPolicy" or not root.findall(".//Grant"):
            raise ArchiveError("Cannot verify the bucket ACL")
        if any(grant.findtext("Grantee/URI") for grant in root.findall(".//Grant")):
            raise ArchiveError("Bucket ACL permits group access; a private bucket is required")
        try:
            raw = self._small(self._request("GET", query={"policy": ""}))
            try:
                policy = json.loads(raw)
                statements = policy["Statement"]
                if not isinstance(statements, list):
                    raise ValueError
                for statement in statements:
                    if (statement["Effect"] == "Allow" and
                            ("NotPrincipal" in statement or public_principal(statement.get("Principal")))):
                        raise ArchiveError("Bucket policy permits public access")
            except (ValueError, KeyError, TypeError):
                raise ArchiveError("Cannot verify the bucket policy") from None
        except RequestError as exc:
            if not (exc.status == 404 and exc.code == "NoSuchBucketPolicy"):
                raise
        root = parse_xml(self._small(self._request("GET", query={"versioning": ""})))
        if root.tag != "VersioningConfiguration" or root.findtext("Status") != "Enabled":
            raise ArchiveError("Bucket versioning must be Enabled")

    def head(self, key: str, version_id: str | None = None, *, include_integrity_metadata: bool = False):
        validate_key(key, scope=self.config.scope)
        with self._request("HEAD", key=key, query=validate_version(version_id)) as response:
            try:
                size = int(response.headers["Content-Length"])
            except (KeyError, TypeError, ValueError):
                raise ArchiveError("S3 did not return a valid object size") from None
            if size < 1 or size > self.config.max_bytes:
                raise ArchiveError("Object size exceeds the archive size bounds")
            actual_version = response.headers.get("x-amz-version-id")
            validate_version(actual_version or "null")
            if version_id is not None and actual_version != version_id:
                raise ArchiveError("S3 returned a different object version")
            metadata = {"bucket": self.config.bucket, "key": key,
                        "version_id": actual_version, "size_bytes": size}
            if include_integrity_metadata:
                sha256 = response.headers.get("x-amz-meta-sha256", "")
                if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                    raise ArchiveError("S3 object has no valid SHA-256 metadata")
                modified = parse_timestamp(response.headers.get("Last-Modified"), http_date=True)
                metadata.update({"sha256": sha256, "last_modified": modified.isoformat()})
            return metadata

    def latest(self, artifact_kind: str = "full", lookback_days: int = 3):
        """Find the newest artifact by bounded daily LISTs and a versioned HEAD.

        This reports object presence/freshness, not successful restoration or a
        new content-integrity check. Truncated or failed listings are unknown,
        never missing. No legacy unclassified prefix is treated as a full backup.
        """
        prefix = artifact_prefix(artifact_kind, scope=self.config.scope)
        if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or not 1 <= lookback_days <= 31:
            raise ArchiveError("Lookback must be between 1 and 31 UTC calendar days")
        checked_at = dt.datetime.now(dt.timezone.utc)
        result = {"schema": f"rbx.{self.config.scope}.archive-latest.v1", "artifact_kind": artifact_kind,
                  "bucket": self.config.bucket, "endpoint": self.config.endpoint.rstrip("/"),
                  "checked_at": checked_at.isoformat(), "lookback_days": lookback_days,
                  "list_requests": 0}
        candidates = []
        try:
            for offset in range(lookback_days):
                day = checked_at.date() - dt.timedelta(days=offset)
                daily_prefix = prefix + day.isoformat() + "/"
                result["list_requests"] += 1
                # No retries for LIST: the number of HTTP LIST calls is bounded
                # by lookback_days, even during a provider outage.
                body = self._small(self._request("GET", query={"list-type": "2", "max-keys": "1000",
                                                              "prefix": daily_prefix},
                                                 max_attempts=1), LIST_MAX_BYTES)
                listing = parse_xml(body)
                if (listing.tag != "ListBucketResult" or listing.findtext("Name") != self.config.bucket
                        or listing.findtext("Prefix") != daily_prefix):
                    raise ArchiveError("S3 returned an unexpected listing scope")
                truncated = listing.findtext("IsTruncated")
                objects = listing.findall("Contents")
                if truncated == "true" or len(objects) > 1000:
                    return {**result, "status": "unknown", "reason": "listing_truncated"}
                try:
                    count = int(listing.findtext("KeyCount"))
                except (TypeError, ValueError):
                    raise ArchiveError("S3 listing has no valid key count") from None
                if truncated != "false" or count != len(objects) or not 0 <= count <= 1000:
                    raise ArchiveError("S3 listing is incomplete or inconsistent")
                for item in objects:
                    key = item.findtext("Key", "")
                    validate_key(key, scope=self.config.scope)
                    if not key.startswith(daily_prefix):
                        raise ArchiveError("S3 returned an object outside the requested prefix")
                    modified = parse_timestamp(item.findtext("LastModified"))
                    try:
                        size = int(item.findtext("Size"))
                    except (ValueError, TypeError):
                        raise ArchiveError("S3 listing has no valid object size") from None
                    if not 1 <= size <= self.config.max_bytes:
                        raise ArchiveError("Listed object size exceeds the archive size bounds")
                    candidates.append((modified, key, size))
            if not candidates:
                return {**result, "status": "missing", "reason": "no_objects_in_window"}
            modified, key, size = max(candidates)
            metadata = self.head(key, include_integrity_metadata=True)
            head_modified = parse_timestamp(metadata["last_modified"])
            if metadata["size_bytes"] != size or int(head_modified.timestamp()) != int(modified.timestamp()):
                raise ArchiveError("S3 object changed between LIST and HEAD")
            observed_at = dt.datetime.now(dt.timezone.utc)
            age = (observed_at - head_modified).total_seconds()
            if age < 0:
                raise ArchiveError("S3 object timestamp is in the future")
            return {**result, **metadata, "status": "found", "checked_at": observed_at.isoformat(),
                    "age_seconds": int(age), "integrity": "metadata_only"}
        except ArchiveError as exc:
            return {**result, "status": "unknown", "reason": "metadata_unavailable", "error": str(exc)}
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            return {**result, "status": "unknown", "reason": "metadata_unavailable",
                    "error": "S3 metadata request failed"}

    def _download_verified(self, key, version_id, expected_sha256, expected_size, stream):
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ArchiveError("An expected SHA-256 hash is required")
        deadline = time.monotonic() + self.config.transfer_seconds
        digest, size = hashlib.sha256(), 0
        with self._request("GET", key=key, query=validate_version(version_id)) as response:
            if response.headers.get("x-amz-version-id") != version_id:
                raise ArchiveError("S3 returned a different object version")
            while True:
                if time.monotonic() > deadline:
                    raise ArchiveError("Archive transfer exceeded its time limit")
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_size or size > self.config.max_bytes:
                    raise ArchiveError("Downloaded archive exceeds its declared size")
                digest.update(chunk)
                if stream is not None:
                    stream.write(chunk)
        if size != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_sha256):
            raise ArchiveError("Downloaded archive SHA-256 or size does not match")

    def upload(self, path: Path, artifact_kind: str = "full"):
        key = new_key(path.suffix, artifact_kind, scope=self.config.scope)
        if not path.is_file() or path.is_symlink():
            raise ArchiveError("Archive must be a regular encrypted file")
        size, digest = 0, hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                size += len(chunk)
                if size > self.config.max_bytes:
                    raise ArchiveError("Archive exceeds the size limit")
                digest.update(chunk)
        if not size:
            raise ArchiveError("Archive must not be empty")
        expected_hash = digest.hexdigest()
        self.check_bucket()
        recovered = False
        put_version = None
        try:
            with path.open("rb") as stream:
                with self._request("PUT", key=key,
                                   headers={"Content-Length": str(size), "Content-Type": "application/octet-stream",
                                            "If-None-Match": "*", "x-amz-meta-sha256": expected_hash},
                                   payload_sha256=expected_hash,
                                   data=TimedReader(stream, time.monotonic() + self.config.transfer_seconds)) as response:
                    put_version = response.headers.get("x-amz-version-id")
        except RequestError as exc:
            if exc.status is not None and exc.status < 500 and exc.status not in (408, 429):
                raise
            # A timeout may occur after S3 stores the object. Never upload again:
            # recover only if HEAD and GET prove this unique key has our bytes.
            recovered = True
        metadata = self.head(key, put_version)
        if metadata["size_bytes"] != size:
            raise ArchiveError("Stored object size does not match the local archive")
        self._download_verified(key, metadata["version_id"], expected_hash, size, None)
        return {"schema": f"rbx.{self.config.scope}.archive-receipt.v1", **metadata, "artifact_kind": artifact_kind,
                "endpoint": self.config.endpoint.rstrip("/"), "sha256": expected_hash,
                "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "recovered_after_ambiguous_put": recovered}

    def download(self, key: str, destination: Path, expected_sha256: str, version_id: str | None = None):
        metadata = self.head(key, version_id)
        if destination.exists():
            raise ArchiveError("Download destination already exists")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{self.config.scope}-download-", dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                self._download_verified(key, metadata["version_id"], expected_sha256, metadata["size_bytes"], stream)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link creation is exclusive: never overwrite a destination
            # that appeared during the download. Both paths are on the same FS.
            os.link(temporary, destination)
        except FileExistsError:
            raise ArchiveError("Download destination already exists") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {**metadata, "sha256": expected_sha256, "verified": True}


def main(argv=None, *, scope="plausible"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--artifact-kind", choices=ARTIFACT_KINDS, default="full")
    download = commands.add_parser("download")
    download.add_argument("key")
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--expected-sha256", required=True)
    download.add_argument("--version-id")
    head = commands.add_parser("head")
    head.add_argument("key")
    head.add_argument("--version-id")
    latest = commands.add_parser("latest")
    latest.add_argument("--artifact-kind", choices=ARTIFACT_KINDS, default="full")
    latest.add_argument("--lookback-days", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        client = ArchiveClient(Config.from_env(max_bytes=args.max_bytes, scope=scope))
        if args.command == "upload":
            result = client.upload(args.file, args.artifact_kind)
        elif args.command == "download":
            result = client.download(args.key, args.output, args.expected_sha256, args.version_id)
        elif args.command == "head":
            result = client.head(args.key, args.version_id)
        else:
            result = client.latest(args.artifact_kind, args.lookback_days)
        print(json.dumps(result, sort_keys=True))
        return 1 if result.get("status") == "unknown" else 0
    except ArchiveError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        # Never print raw exception text, request headers, URLs, or credentials.
        print(json.dumps({"error": "Archive operation failed; inspect local file access and S3 connectivity"}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
