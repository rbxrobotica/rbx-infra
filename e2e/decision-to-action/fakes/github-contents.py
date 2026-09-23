#!/usr/bin/env python3
"""Fake GitHub contents API for the E2E.

Serves GET /repos/{owner}/{repo}/contents/{path}?ref={commit} with
Accept: application/vnd.github.raw+json by reading the object from a local
bare repository (`git show <commit>:<path>`). No auth is checked beyond the
presence of an Authorization header, mirroring what Public Presence sends.
"""
import os, subprocess, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

BARE = os.environ["E2E_BARE_REPO"]

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        parts = u.path.split("/")
        # /repos/{owner}/{repo}/contents/{path...}
        if len(parts) < 6 or parts[1] != "repos" or parts[4] != "contents":
            self.send_response(404); self.end_headers(); return
        if "Authorization" not in self.headers:
            self.send_response(401); self.end_headers(); return
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
