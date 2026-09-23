#!/usr/bin/env python3
"""Fake GitHub contents API for the E2E.

Serves GET /repos/{owner}/{repo}/contents/{path}?ref={commit} with
Accept: application/vnd.github.raw+json by reading the object from a local
bare repository (`git show <commit>:<path>`). No auth is checked beyond the
presence of an Authorization header, mirroring what Public Presence sends.
"""
import json, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

BARE = os.environ["E2E_BARE_REPO"]

class H(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def git(self, *args):
        return subprocess.check_output(["git", "--git-dir", BARE, *args], stderr=subprocess.DEVNULL).decode().strip()

    def do_GET(self):
        u = urlparse(self.path)
        parts = u.path.split("/")
        if len(parts) < 5 or parts[1] != "repos":
            self.send_response(404); self.end_headers(); return
        if "Authorization" not in self.headers:
            self.send_response(401); self.end_headers(); return
        owner, repo = parts[2], parts[3]
        full = f"{owner}/{repo}"
        # /repos/{owner}/{repo}/pulls/{number}: the fake gh created one PR whose head is
        # the newest mission/* branch pushed to the bare repo.
        if len(parts) == 6 and parts[4] == "pulls":
            try:
                ref = self.git("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/mission/").splitlines()[0]
                sha = self.git("rev-parse", ref)
            except (subprocess.CalledProcessError, IndexError):
                self.send_response(404); self.end_headers(); return
            self.send_json(200, {"number": int(parts[5]), "state": "open",
                                 "head": {"sha": sha, "ref": ref, "repo": {"full_name": full}},
                                 "base": {"ref": "main", "repo": {"full_name": full}}})
            return
        # /repos/{owner}/{repo}/compare/{base}...{head}
        if len(parts) == 6 and parts[4] == "compare" and "..." in parts[5]:
            base, head = parts[5].split("...", 1)
            try:
                self.git("cat-file", "-e", f"{base}^{{commit}}"); self.git("cat-file", "-e", f"{head}^{{commit}}")
            except subprocess.CalledProcessError:
                self.send_response(404); self.end_headers(); return
            if base == head or self.git("rev-parse", base) == self.git("rev-parse", head):
                status = "identical"
            elif subprocess.call(["git", "--git-dir", BARE, "merge-base", "--is-ancestor", base, head]) == 0:
                status = "ahead"
            elif subprocess.call(["git", "--git-dir", BARE, "merge-base", "--is-ancestor", head, base]) == 0:
                status = "behind"
            else:
                status = "diverged"
            ahead = int(self.git("rev-list", "--count", f"{base}..{head}")) if status in ("ahead", "diverged") else 0
            self.send_json(200, {"status": status, "ahead_by": ahead, "behind_by": 0})
            return
        # /repos/{owner}/{repo}/contents/{path...}
        if len(parts) < 6 or parts[4] != "contents":
            self.send_response(404); self.end_headers(); return
        path = "/".join(parts[5:])
        ref = parse_qs(u.query).get("ref", ["HEAD"])[0]
        try:
            data = subprocess.check_output(["git", "--git-dir", BARE, "show", f"{ref}:{path}"], stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.github.raw+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv = HTTPServer(("127.0.0.1", port), H)
    print(srv.server_address[1], flush=True)
    srv.serve_forever()
