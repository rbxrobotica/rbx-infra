#!/usr/bin/env python3
"""Acquire only the official distribution f3write/f3read binaries as a user.

Run under rbx-build-cache; downloaded/extracted files are disposable. The frozen
executables and provenance manifest are important outputs on the primary SSD.
No package install, root privilege or raw-device utility is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

VERSION = '8.0-2build2'
DEST = Path.home()/'.local/share/rbx-recovery-tools'/('f3-'+VERSION)


def main():
    os.umask(0o077)
    if DEST.exists():
        raise SystemExit('Preserve existing tool release; inspect its manifest before reuse')
    fields = subprocess.check_output(['apt-cache', 'show', 'f3='+VERSION], text=True)
    expected = next(line.split(': ', 1)[1] for line in fields.splitlines()
                    if line.startswith('SHA256: '))
    with tempfile.TemporaryDirectory(prefix='rbx-f3-package-') as scratch:
        scratch = Path(scratch)
        subprocess.run(['apt-get', 'download', 'f3='+VERSION], cwd=scratch, check=True)
        packages = list(scratch.glob('f3_*.deb'))
        if len(packages) != 1:
            raise ValueError('Unexpected package set')
        package = packages[0]
        if hashlib.sha256(package.read_bytes()).hexdigest() != expected:
            raise ValueError('Distribution package checksum mismatch')
        subprocess.run(['dpkg-deb', '-x', str(package), str(scratch/'extracted')], check=True)
        DEST.mkdir(parents=True, mode=0o700)
        manifest = {'package': 'f3', 'version': VERSION, 'package_sha256': expected,
                    'source': 'configured Ubuntu Noble signed APT package metadata',
                    'upstream': 'https://github.com/AltraMayor/f3', 'files': {}}
        for name in ('f3write', 'f3read'):
            source = scratch/'extracted/usr/bin'/name
            target = DEST/name
            shutil.copyfile(source, target)
            target.chmod(0o500)
            manifest['files'][name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (DEST/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
