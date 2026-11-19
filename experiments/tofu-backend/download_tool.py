#!/usr/bin/env python3
"""Download the exact public engine under the caller's validated cache lease."""
import hashlib,json,os,subprocess,urllib.request,zipfile
from pathlib import Path

pins=json.loads(Path(__file__).with_name('pins.json').read_text())
root=Path(os.environ['RBX_DISPOSABLE_CACHE_ROOT'])/'opentofu-qualification'/pins['version']
root.mkdir(parents=True,exist_ok=True)
base='https://github.com/opentofu/opentofu/releases/download/v'+pins['version']+'/'
for filename,expected in [(pins['archive'],pins['archive_sha256']),('tofu_'+pins['version']+'_SHA256SUMS',pins['checksums_sha256'])]:
    target=root/filename
    if not target.exists():
        with urllib.request.urlopen(base+filename,timeout=45) as response:
            if not response.url.startswith('https://'):raise ValueError('non_tls_download')
            data=response.read(40*1024*1024+1)
        if len(data)>40*1024*1024:raise ValueError('download_bound')
        if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('download_digest_mismatch')
        target.write_bytes(data)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise ValueError('cached_digest_mismatch')
checksums=(root/('tofu_'+pins['version']+'_SHA256SUMS')).read_text()
if pins['archive_sha256']+'  '+pins['archive'] not in checksums:raise ValueError('checksum_file_disagrees')
with zipfile.ZipFile(root/pins['archive']) as archive:
    info=archive.getinfo('tofu')
    if info.file_size>150*1024*1024:raise ValueError('binary_bound')
    target=root/'tofu';target.write_bytes(archive.read('tofu'));target.chmod(0o700)
result=subprocess.run([str(target),'version','-json'],capture_output=True,text=True,check=True,timeout=10)
version=json.loads(result.stdout)
if version['terraform_version']!=pins['version']:raise ValueError('engine_version_mismatch')
print(json.dumps({'binary':str(target),'version':version['terraform_version'],'binary_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'archive_sha256':pins['archive_sha256'],'checksum_file_verified':True,'signature_verified':False,'source':pins['source']}))
