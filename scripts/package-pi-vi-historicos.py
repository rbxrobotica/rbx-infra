#!/usr/bin/env python3
"""Package the already checked static frontend reproducibly for its ConfigMap."""
from pathlib import Path
import gzip
import hashlib
import io
import tarfile

root = Path(__file__).resolve().parents[1] / "apps/prod/pi-vi-historicos"
build = root / "frontend/build"
if not (build / "index.html").exists():
    raise SystemExit("Build frontend first: npm ci && npm run check && npm test && npm run build")
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
    for path in sorted(build.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            info = tarfile.TarInfo(str(path.relative_to(build)))
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            archive.addfile(info, io.BytesIO(data))
data = gzip.compress(buffer.getvalue(), mtime=0)
if len(data) > 700_000:
    raise SystemExit("Build exceeds preview ConfigMap budget; use a container or object store instead")
(root / "site.tgz").write_bytes(data)
print(hashlib.sha256(data).hexdigest(), "site.tgz", len(data), "bytes")
