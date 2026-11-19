#!/usr/bin/env python3
"""Run an isolated PostgreSQL logical recovery rehearsal on ThinkCentre."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import zipfile

HERE = Path(__file__).resolve().parent
HOST = "psyctl-ThinkCentre-E73"
PEER = "psyctl-workstation"
ROOT = Path.home() / ".local/share/rbx-agent-handoffs/pg16n19"
DIGEST = "sha256:075f7ba66bc9b3ce7d6b8b635208ff61cd7cf1a67d71ec530eec5d7ae0cbe571"
IMAGE = "docker.io/library/postgres@" + DIGEST
IMAGE_ID = "75f5a96988cdf694a215073c3e9c001b706b371e2f94df3967f2efdec2787f6b"
VERSION = "16.15"
GIT_REMOTE = "git@github.com:rbxrobotica/rbx-infra.git"
MEMBERS = {"fixture.dump", "roles.sql", "manifest.json"}
MAX_BYTES = 12 * 1024 * 1024
TOTAL_SECONDS = 180
START = r"""
set -eu
umask 077
mkdir /lab/db /lab/socket
initdb -D /lab/db -U lab_admin --encoding=UTF8 --locale=C --no-instructions   --auth-local=trust --auth-host=reject
exec postgres -D /lab/db -k /lab/socket -p 55416 -c listen_addresses=''   -c max_connections=8 -c shared_buffers=16MB -c max_wal_size=64MB   -c timezone=UTC -c unix_socket_permissions=0700 -c statement_timeout=15000   -c lock_timeout=3000
"""
DB_ENV = (
    "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
    "LC_ALL=C", "TZ=UTC", "PGCLIENTENCODING=UTF8", "PGCONNECT_TIMEOUT=3",
)
SSH = (
    "/usr/bin/ssh", "-T", "-oBatchMode=yes", "-oConnectTimeout=5",
    "-oStrictHostKeyChecking=yes", "-oForwardAgent=no", "-oClearAllForwardings=yes",
    "-oServerAliveInterval=3", "-oServerAliveCountMax=2", "rbx-local",
)
REMOTE = """set -eu
umask 077
set -C
test "$(hostname)" = psyctl-workstation
test "$(id -un)" = psyctl
base="$HOME/.local/share/rbx-agent-handoffs/pg16n19"
test -d "$base" && test ! -L "$base" && test -O "$base"
"""


class LabError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise LabError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def elapsed(start):
    return round(time.monotonic() - start, 6)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def metadata(value):
    return {"bytes": len(value), "sha256": digest(value)}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def verify_blob(value, expected, label):
    require(0 < len(value) <= MAX_BYTES and metadata(value) == expected,
            f"{label} size or trusted SHA-256 mismatch")


def package(members):
    require(members.keys() == MEMBERS, "archive members mismatch")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name), content)
    value = output.getvalue()
    require(len(value) <= MAX_BYTES, "archive exceeds lab size bound")
    return value


def unpack(value, expected):
    require(isinstance(expected, dict) and expected.keys() == MEMBERS,
            "missing trusted member hash or manifest")
    require(len(value) <= MAX_BYTES, "archive exceeds lab size bound")
    try:
        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            entries = archive.infolist()
            require({entry.filename for entry in entries} == MEMBERS and
                    len(entries) == len(MEMBERS) and
                    all(entry.compress_type == zipfile.ZIP_STORED for entry in entries) and
                    sum(entry.file_size for entry in entries) <= MAX_BYTES,
                    "archive structure mismatch")
            contents = {entry.filename: archive.read(entry) for entry in entries}
    except (zipfile.BadZipFile, EOFError, RuntimeError) as error:
        if isinstance(error, LabError):
            raise
        raise LabError("invalid closed ZIP") from error
    for name, data in contents.items():
        verify_blob(data, expected[name], name)
    return contents


def command(args, data=None, *, timeout=15, check=True, quiet=False):
    result = subprocess.run(
        args, input=b"" if data is None else data, capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode:
        detail = "" if quiet else result.stderr.decode(errors="replace").strip()[-800:]
        raise LabError(f"{Path(args[0]).name} exited {result.returncode}: {detail}")
    return result


def origin():
    return command(("/usr/bin/git", "-C", str(HERE), "remote", "get-url", "origin")).stdout.decode().strip()


def repo(args):
    return command(("/usr/bin/git", "-C", str(HERE), *args)).stdout.decode().strip()


def preflight():
    require(os.getuid() != 0 and os.uname().nodename == HOST, "unexpected runtime host or user")
    require(Path.home() == Path("/home/psyctl"), "unexpected home")
    info = json.loads(command(("/usr/bin/podman", "info", "--format", "json")).stdout)
    host = info["host"]
    require(host["security"]["rootless"] and host["arch"] == "amd64" and
            host["cgroupVersion"] == "v2" and
            {"cpu", "memory", "pids"} <= set(host["cgroupControllers"]),
            "rootless runtime or resource controllers unavailable")
    local = command(("/usr/bin/podman", "image", "inspect", IMAGE,
                     "--format", "{{.Id}} {{.Digest}}")).stdout.decode().split()
    require(local == [IMAGE_ID, DIGEST], "immutable image/config mismatch")
    root_status = ROOT.stat()
    require(ROOT.resolve() == ROOT and root_status.st_uid == os.getuid() and
            stat.S_IMODE(root_status.st_mode) == 0o700, "unsafe local custody root")
    fs = os.statvfs(ROOT)
    require(fs.f_bavail * fs.f_frsize >= 1024**3, "less than 1 GiB available")
    commit = repo(("rev-parse", "HEAD"))
    require(origin() == GIT_REMOTE and not repo(("status", "--porcelain", "--", ".")),
            "experiment must run from clean committed source at the expected origin")
    upstream = repo(("rev-parse", "@{upstream}"))
    ssh('test "$(stat -c %a "$base")" = 700')
    require(commit == upstream, "run commit must equal the fetched remote checkpoint")
    return {
        "host": HOST, "peer": PEER, "source_commit": commit, "image": IMAGE,
        "image_config": IMAGE_ID, "server_required": VERSION, "rootless": True,
        "cgroup": "v2", "available_bytes": fs.f_bavail * fs.f_frsize,
        "podman_version": command(("/usr/bin/podman", "--version")).stdout.decode().strip(),
        "gpg_version": command(("/usr/bin/gpg", "--version")).stdout.decode().splitlines()[0],
    }


class Cluster:
    def __init__(self, nonce, phase):
        self.name = f"pg16n19-{nonce}-{phase}"
        self.cid = None
        self.system_id = None

    def create(self):
        result = command((
            "/usr/bin/podman", "create", "--name", self.name,
            "--label", f"rbx.lab.identity={self.name}", "--pull=never",
            "--network=none", "--ipc=private", "--pid=private",
            "--cap-drop=all", "--security-opt=no-new-privileges",
            "--read-only", "--read-only-tmpfs=false", "--image-volume=ignore",
            "--tmpfs", "/lab:rw,nodev,nosuid,noexec,size=192m,mode=0700,uid=70,gid=70",
            "--shm-size=16m", "--user=70:70", "--memory=384m",
            "--memory-swap=384m", "--cpus=1", "--pids-limit=48",
            "--entrypoint=/bin/sh", IMAGE_ID, "-c", START,
        ))
        self.cid = result.stdout.decode().strip()
        require(re.fullmatch(r"[0-9a-f]{64}", self.cid), "invalid container ID")
        self.owned()
        command(("/usr/bin/podman", "start", self.cid))
        ready_by = time.monotonic() + 20
        while time.monotonic() < ready_by:
            result = self.exec("pg_isready", "-q", "-t", "1", *self.endpoint("postgres"),
                               check=False)
            if result.returncode == 0:
                break
            time.sleep(0.25)
        else:
            logs = command(("/usr/bin/podman", "logs", "--tail", "15", self.cid),
                           check=False)
            raise LabError("fixture did not start: " +
                           (logs.stdout + logs.stderr).decode(errors="replace")[-1100:])
        self.system_id = self.check_endpoint()
        versions = {tool: self.exec(tool, "--version").stdout.decode().strip()
                    for tool in ("psql", "pg_dump", "pg_restore")}
        require(all(re.search(r"\b" + re.escape(VERSION) + r"\b", v)
                    for v in versions.values()), "PostgreSQL client version mismatch")
        return {"container": self.name, "id": self.cid, "system_identifier": self.system_id,
                "server": VERSION, "clients": versions, "pgdata": "/lab/db",
                "socket": "/lab/socket", "socket_port": 55416}

    def owned(self):
        result = command((
            "/usr/bin/podman", "inspect", "--type", "container", "--format",
            '{{.Id}} {{.Name}} {{index .Config.Labels "rbx.lab.identity"}} '
            '{{.Image}} {{.HostConfig.NetworkMode}} {{.HostConfig.Privileged}}',
            self.cid,
        )).stdout.decode().split()
        require(result == [self.cid, self.name, self.name, IMAGE_ID, "none", "false"],
                "container identity or isolation mismatch")

    def exec(self, *args, data=None, check=True):
        self.owned()
        return command(("/usr/bin/podman", "exec", "-i", self.cid, *DB_ENV, *args),
                       data, check=check)

    @staticmethod
    def endpoint(db, user="lab_admin"):
        return ("--host=/lab/socket", "--port=55416", f"--username={user}", f"--dbname={db}")

    def psql(self, db, sql, user="lab_admin", check=True):
        return self.exec("psql", "-X", "-w", "-qAt", "-v", "ON_ERROR_STOP=1",
                         "-v", "VERBOSITY=sqlstate", *self.endpoint(db, user),
                         data=sql, check=check)

    def check_endpoint(self):
        data = self.psql("postgres", b"""
SELECT current_database(), session_user, current_setting('server_version_num'),
       current_setting('data_directory'), current_setting('unix_socket_directories'),
       current_setting('listen_addresses'), inet_server_addr() IS NULL,
       current_setting('port'), system_identifier FROM pg_control_system();
""").stdout.decode().strip().split("|")
        require(data[:-1] == ["postgres", "lab_admin", "160015", "/lab/db",
                              "/lab/socket", "", "t", "55416"] and
                data[-1].isdigit() and (self.system_id is None or data[-1] == self.system_id),
                "fixture endpoint identity mismatch")
        return data[-1]

    def mutate(self, db, sql, user="lab_admin", check=True):
        self.check_endpoint()
        before = self.psql(db, b"SELECT current_database(), session_user, inet_server_addr() IS NULL;",
                           user).stdout.decode().strip()
        require(before == f"{db}|{user}|t", "database or role identity mismatch")
        return self.psql(db, sql, user, check)

    def remove(self):
        if self.cid is not None:
            self.owned()
            command(("/usr/bin/podman", "rm", "-f", "--time=3", self.cid))
            self.cid = None


def capture_manifest(cluster):
    cluster.check_endpoint()
    schema = cluster.exec("pg_dump", "-w", "--schema-only", "--create",
                          *cluster.endpoint("lab_fixture")).stdout
    # Recent PostgreSQL clients emit a random psql restriction token per dump.
    schema = re.sub(rb"(?m)^\\(?:un)?restrict [A-Za-z0-9]+\n", b"", schema)
    manifest = {
        "schema": metadata(schema),
        "catalog": [json.loads(line) for line in
                    cluster.psql("lab_fixture", (HERE / "manifest.sql").read_bytes()).stdout.splitlines()],
        "rows": {},
    }
    for table in ("categories", "entries"):
        sql = f"COPY (SELECT * FROM sample.{table} ORDER BY id) TO STDOUT WITH (FORMAT csv);"
        result = cluster.psql("lab_fixture", sql.encode()).stdout
        manifest["rows"][table] = {"count": result.count(b"\n"), **metadata(result)}
    require(manifest["rows"]["entries"]["count"] == 2400 and
            1024**2 < manifest["rows"]["entries"]["bytes"] < 10 * 1024**2 and
            any(entry.get("extname") == "hstore" for entry in manifest["catalog"]),
            "fixture data, size, or extension mismatch")
    return canonical(manifest)


def require_toc(cluster, dump):
    listing = cluster.exec("pg_restore", "--list", data=dump).stdout
    require(re.search(rb"(?m)^\d+; \d+ \d+ EXTENSION - hstore(?: |$)", listing),
            "required hstore extension missing from dump")
    require(re.search(rb"(?m)^\d+; \d+ \d+ TABLE sample entries lab_owner$", listing),
            "fixture entries or ownership missing from dump")


def require_roles(cluster):
    rows = cluster.psql("postgres", b"""
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication
FROM pg_roles WHERE rolname IN ('lab_owner','lab_reader') ORDER BY rolname;
""").stdout
    require(rows == b"lab_owner|f|f|f|f|f|f\nlab_reader|t|f|f|f|f|f\n",
            "required fixture roles missing or overprivileged")


def rejection(receipt, name, expected, operation):
    started = time.monotonic()
    try:
        operation()
    except LabError as error:
        require(expected in str(error), f"{name}: unexpected rejection: {error}")
        receipt["negative"][name] = {"result": "rejected", "reason": str(error),
                                     "seconds": elapsed(started)}
        return
    raise LabError(f"{name}: unexpectedly accepted")


def require_sqlstate(result, code):
    require(result.returncode != 0 and f"ERROR:  {code}".encode() in result.stderr,
            f"expected SQLSTATE {code}, got rc={result.returncode}, stderr={result.stderr[-300:]!r}")


def functional_read(cluster):
    result = cluster.psql("lab_fixture", """
SELECT session_user, count(*), sum(amount),
       (SELECT label FROM sample.entries WHERE id = 1),
       (SELECT recorded_at FROM sample.entries WHERE id = 1),
       (SELECT octet_length(payload) FROM sample.entries WHERE id = 1),
       (SELECT properties OPERATOR(public.->) 'fixture' FROM sample.entries WHERE id = 1)
FROM sample.entries;
""".encode(), "lab_reader").stdout.decode().strip()
    require(result == "lab_reader|2400|3543876.00|ação 東京 1|2026-11-19 12:00:01+00|1024|sintético",
            "functional reader result mismatch")
    return result


def ssh(remote, data=None):
    return command((*SSH, REMOTE + remote), data, timeout=18).stdout


def gpg(home, key, mode, data):
    args = ("/usr/bin/gpg", "--no-options", "--homedir", str(home), "--batch",
            "--no-tty", "--pinentry-mode", "loopback", "--no-symkey-cache",
            "--passphrase-file", str(key), "--output", "-")
    options = ("--cipher-algo", "AES256", "--compress-algo", "none", "--symmetric") \
        if mode == "encrypt" else ("--decrypt",)
    result = command((*args, *options), data, quiet=True)
    return result.stdout


def read_verified(cipher, receipt, receipt_hash, home, key):
    require(re.fullmatch(r"[0-9a-f]{64}", receipt_hash or "") and digest(receipt) == receipt_hash,
            "missing or incorrect trusted receipt hash")
    record = json.loads(receipt)
    require(isinstance(record.get("members"), dict) and record["members"].keys() == MEMBERS,
            "missing trusted member hash or manifest")
    verify_blob(cipher, record["ciphertext"], "ciphertext")
    plain = gpg(home, key, "decrypt", cipher)
    verify_blob(plain, record["archive"], "decrypted archive")
    return unpack(plain, record["members"])


def run(preparation):
    nonce = secrets.token_hex(8)
    require(re.fullmatch(r"[0-9a-f]{16}", nonce), "invalid nonce")
    run_dir = ROOT / f"run-{nonce}"
    key_dir = ROOT / f"key-{nonce}"
    source = Cluster(nonce, "source")
    target = Cluster(nonce, "target")
    run_dir.mkdir(mode=0o700)
    evidence = {"nonce": nonce, "preflight": preparation, "started_utc": utc(),
                "negative": {}, "seconds": {}, "identities": {}, "cleanup": {},
                "receipt_directory": f"rbx-local:~/.local/share/rbx-agent-handoffs/pg16n19/archive-{nonce}",
                "key_directory": str(key_dir), "result": "failed"}
    stage = "lab-key preparation"
    started = time.monotonic()
    succeeded = False
    try:
        phase = time.monotonic()
        key_dir.mkdir(mode=0o700)
        gpg_home = key_dir / "gnupg"
        gpg_home.mkdir(mode=0o700)
        key = key_dir / "passphrase"
        key.write_bytes(secrets.token_hex(32).encode() + b"\n")
        evidence["seconds"]["lab_key_preparation"] = elapsed(phase)

        stage = "source provisioning and fixture load"
        phase = time.monotonic()
        evidence["identities"]["source"] = source.create()
        evidence["seconds"]["source_provisioning"] = elapsed(phase)
        phase = time.monotonic()
        roles = (HERE / "roles.sql").read_bytes()
        source.mutate("postgres", roles)
        source.mutate("postgres", (HERE / "database.sql").read_bytes())
        source.mutate("lab_fixture", (HERE / "fixture.sql").read_bytes())
        cutoff = time.monotonic()
        evidence["cutoff_utc"] = utc()
        evidence["seconds"]["fixture_load"] = elapsed(phase)

        stage = "capture and close archive"
        phase = time.monotonic()
        evidence["capture_start_utc"] = utc()
        require_roles(source)
        manifest = capture_manifest(source)
        evidence["manifest"] = json.loads(manifest)
        dump = source.exec("pg_dump", "-w", "--format=custom", "--create",
                           *source.endpoint("lab_fixture")).stdout
        require_toc(source, dump)
        members = {"fixture.dump": dump, "roles.sql": roles, "manifest.json": manifest}
        plain = package(members)
        evidence["capture_end_utc"] = utc()
        evidence["seconds"]["capture"] = elapsed(phase)
        phase = time.monotonic()
        cipher = gpg(gpg_home, key, "encrypt", plain)
        verify_blob(cipher, metadata(cipher), "closed ciphertext")
        receipt = canonical({
            "nonce": nonce, "source_commit": preparation["source_commit"],
            "cutoff_utc": evidence["cutoff_utc"], "capture_start_utc": evidence["capture_start_utc"],
            "capture_end_utc": evidence["capture_end_utc"], "archive": metadata(plain),
            "ciphertext": metadata(cipher), "members": {name: metadata(body)
                                                        for name, body in members.items()},
            "source_system_identifier": source.system_id, "image": IMAGE,
        })
        receipt_hash = digest(receipt)
        evidence["closed_receipt"] = json.loads(receipt)
        evidence["closed_receipt_sha256"] = receipt_hash
        evidence["seconds"]["encrypt_and_close"] = elapsed(phase)

        stage = "off-host transfer"
        phase = time.monotonic()
        remote = f'd="$base/archive-{nonce}"\n'
        ssh(remote + 'mkdir -- "$d"')
        for filename, body in (("archive.gpg", cipher), ("receipt.json", receipt)):
            observed = ssh(remote + f'test -O "$d" && test ! -L "$d"\n'
                           f'head -c {MAX_BYTES + 1} > "$d/{filename}"\n'
                           f'wc -c < "$d/{filename}"\nsha256sum "$d/{filename}"', body)
            size, checksum, _ = observed.decode().split()
            require((int(size), checksum) == (len(body), digest(body)), "upload readback mismatch")
        evidence["off_host_copy_verified_utc"] = utc()
        evidence["seconds"]["cutoff_to_verified_off_host_copy"] = elapsed(cutoff)
        evidence["seconds"]["upload_and_remote_readback"] = elapsed(phase)
        del cipher, receipt, plain, members, dump

        stage = "stop and remove source"
        phase = time.monotonic()
        source.remove()
        evidence["cleanup"]["source_removed_before_retrieval"] = True
        evidence["seconds"]["source_shutdown"] = elapsed(phase)

        stage = "independent retrieval"
        recovery = time.monotonic()
        evidence["retrieval_start_utc"] = utc()
        receipt = ssh(remote + 'head -c 65537 -- "$d/receipt.json"')
        cipher = ssh(remote + f'head -c {MAX_BYTES + 1} -- "$d/archive.gpg"')
        evidence["seconds"]["retrieval"] = elapsed(recovery)
        stage = "trusted verification and decryption"
        phase = time.monotonic()
        restored = read_verified(cipher, receipt, receipt_hash, gpg_home, key)
        evidence["seconds"]["verification_and_decryption"] = elapsed(phase)

        stage = "target provisioning"
        phase = time.monotonic()
        evidence["identities"]["target"] = target.create()
        require(target.system_id != evidence["identities"]["source"]["system_identifier"],
                "target reused source database state")
        evidence["seconds"]["target_provisioning"] = elapsed(phase)
        stage = "missing-role negative and restore"
        phase = time.monotonic()
        rejection(evidence, "missing_role", "required fixture roles missing",
                  lambda: require_roles(target))
        evidence["seconds"]["pre_restore_missing_role_check"] = elapsed(phase)
        phase = time.monotonic()
        require_toc(target, restored["fixture.dump"])
        available = target.psql("postgres", b"""
SELECT default_version FROM pg_available_extensions WHERE name = 'hstore';
""").stdout.strip().decode()
        expected_extension = next(e["extversion"] for e in evidence["manifest"]["catalog"]
                                  if e.get("extname") == "hstore")
        require(available == expected_extension, "required hstore extension version unavailable")
        require(restored["roles.sql"] == roles, "role source mismatch")
        target.mutate("postgres", restored["roles.sql"])
        require_roles(target)
        target.check_endpoint()
        target.exec("pg_restore", "-w", "--exit-on-error", "--create",
                    *target.endpoint("postgres"), data=restored["fixture.dump"])
        evidence["seconds"]["restore"] = elapsed(phase)
        stage = "reconciliation and functional reader"
        phase = time.monotonic()
        reconciled = capture_manifest(target)
        require(reconciled == restored["manifest.json"], "restored manifest differs from source")
        evidence["reconciled_manifest_sha256"] = digest(reconciled)
        evidence["seconds"]["manifest_reconciliation"] = elapsed(phase)
        phase = time.monotonic()
        evidence["functional_read"] = functional_read(target)
        evidence["first_functional_read_utc"] = utc()
        evidence["seconds"]["first_functional_reader_query"] = elapsed(phase)
        evidence["seconds"]["retrieval_to_functional_read"] = elapsed(recovery)

        stage = "negative archive, extension and privilege cases"
        phase = time.monotonic()
        damaged = cipher[:len(cipher) // 2]
        corrupted = cipher[:-8] + bytes([cipher[-8] ^ 1]) + cipher[-7:]
        for name, candidate in (("truncated", damaged), ("corrupted", corrupted)):
            rejection(evidence, name, "ciphertext size or trusted SHA-256 mismatch",
                      lambda: read_verified(candidate, receipt, receipt_hash, gpg_home, key))
            rejection(evidence, f"gpg_{name}", "gpg exited",
                      lambda: gpg(gpg_home, key, "decrypt", candidate))
        for name, bad_hash in (("missing_receipt_hash", ""), ("incorrect_receipt_hash", "0" * 64)):
            rejection(evidence, name, "trusted receipt hash",
                      lambda: read_verified(cipher, receipt, bad_hash, gpg_home, key))
        incomplete = json.loads(receipt)
        del incomplete["members"]["manifest.json"]
        incomplete = canonical(incomplete)
        rejection(evidence, "missing_trusted_manifest", "missing trusted member",
                  lambda: read_verified(cipher, incomplete, digest(incomplete), gpg_home, key))
        wrong_key = key_dir / "wrong-passphrase"
        wrong_key.write_bytes(secrets.token_hex(32).encode() + b"\n")
        try:
            rejection(evidence, "wrong_lab_key", "gpg exited",
                      lambda: read_verified(cipher, receipt, receipt_hash, gpg_home, wrong_key))
        finally:
            wrong_key.unlink()

        # Drop hstore only inside an uncommitted fixture transaction. Error and
        # connection close roll it back. SQLSTATE P0001 identifies our guard.
        require_sqlstate(target.mutate("lab_fixture", b"""
BEGIN;
DROP EXTENSION hstore CASCADE;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'hstore') THEN
        RAISE EXCEPTION 'required hstore missing';
    END IF;
END $$;
ROLLBACK;
""", check=False), "P0001")
        evidence["negative"]["missing_extension"] = {"result": "rejected", "sqlstate": "P0001"}
        for name, sql in {
            "reader_insert": b"INSERT INTO sample.entries DEFAULT VALUES;",
            "reader_create": b"CREATE TABLE sample.blocked(id integer);",
        }.items():
            require_sqlstate(target.mutate("lab_fixture", sql, "lab_reader", check=False), "42501")
            evidence["negative"][name] = {"result": "rejected", "sqlstate": "42501"}
        require(capture_manifest(target) == reconciled and
                functional_read(target) == evidence["functional_read"],
                "target changed during negative tests")
        evidence["historical_archive_policy"] = ("Integrity verified independently of age; "
                                                 "historical archives remain restorable.")
        evidence["seconds"]["negative_checks_and_final_read"] = elapsed(phase)
        succeeded = True
    except (LabError, subprocess.SubprocessError, OSError, ValueError) as error:
        evidence["failure"] = {"stage": stage, "type": type(error).__name__, "detail": str(error)}
    finally:
        for name, cluster in (("source", source), ("target", target)):
            try:
                cluster.remove()
                evidence["cleanup"][name] = "no owned container remains"
            except (LabError, subprocess.SubprocessError, OSError) as error:
                evidence["cleanup"][name] = f"cleanup failed: {error}"
                succeeded = False
        evidence["finish_utc"] = utc()
        evidence["seconds"]["total"] = elapsed(started)
        if succeeded and evidence["seconds"]["total"] <= TOTAL_SECONDS:
            evidence["result"] = "pass"
        output = run_dir / ("success.json" if evidence["result"] == "pass" else "failure.json")
        output.write_bytes(canonical(evidence))
        print(output)
    return 0 if evidence["result"] == "pass" else 1


def interrupt(_signum, _frame):
    raise LabError("rehearsal interrupted or deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dry-run", "run"))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        preparation = preflight()
        if args.mode == "dry-run":
            print(canonical(preparation).decode(), end="")
            return 0
        signal.signal(signal.SIGALRM, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        signal.alarm(TOTAL_SECONDS)
        try:
            return run(preparation)
        finally:
            signal.alarm(0)
    except (LabError, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f"lab refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
