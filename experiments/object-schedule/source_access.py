#!/usr/bin/env python3
"""Owner-only source admission. No owner credential may enter the worker."""
import json
import re
import subprocess
import urllib.parse
import urllib.request
import uuid

BASE = 'https://api.contabo.com/v1/'
IDENTITY_ENTRY = 'rbx/object-backup/source-identity'
READ_BUCKET = ['s3:ListBucket', 's3:GetBucketLocation', 's3:GetBucketVersioning',
               's3:GetBucketAcl', 's3:GetBucketPolicy', 's3:GetLifecycleConfiguration',
               's3:GetEncryptionConfiguration', 's3:GetBucketObjectLockConfiguration']
READ_OBJECT = ['s3:GetObject', 's3:GetObjectTagging']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect_refused')


def stored(ref):
    return subprocess.run(['pass', 'show', ref], check=True, capture_output=True,
                          timeout=30).stdout


def checkpoint(ref, value):
    if not ref.startswith('rbx/object-backup/') or len(ref.split('/')) != 3:
        raise ValueError('unexpected_checkpoint_scope')
    raw = json.dumps(value, sort_keys=True).encode()
    subprocess.run(['pass', 'insert', '--force', '--multiline', ref], input=raw,
                   check=True, capture_output=True, timeout=45)
    if json.loads(stored(ref)) != value:
        raise ValueError('encrypted_custody_mismatch')
    subprocess.run(['pass', 'git', 'push', 'origin', 'HEAD'], check=True,
                   capture_output=True, timeout=60)


class Owner:
    def __init__(self):
        entry = stored('server/contabo-panel').decode().splitlines()
        username = next((x.split(':', 1)[1].strip() for x in entry[1:]
                         if x.startswith('username:')), 'ldamasio@gmail.com')
        data = urllib.parse.urlencode({
            'client_id': stored('rbx/contabo/client-id').decode().strip(),
            'client_secret': stored('rbx/contabo/client-secret').decode().strip(),
            'username': username, 'password': entry[0], 'grant_type': 'password'}).encode()
        request = urllib.request.Request(
            'https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token', data=data)
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            self.token = json.loads(response.read(1024 * 1024))['access_token']

    def api(self, path, payload=None, method=None):
        if not re.fullmatch(r'[a-zA-Z0-9/?=&_.%-]+', path) or path.startswith('/'):
            raise ValueError('unexpected_control_path')
        if payload is None and method in ('PATCH', 'DELETE'):
            payload = {}
        request = urllib.request.Request(BASE + path, method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json', 'x-request-id': str(uuid.uuid4())},
            data=json.dumps(payload).encode() if payload is not None else None)
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('oversized_control_response')
        return json.loads(raw) if raw else {}


def admission_role():
    return {'name': 'RBX Memory Backup Admission', 'admin': False,
            'accessAllResources': False, 'permissions': [
                {'apiName': '/object-storage/s3', 'actions': ['READ'], 'resources': []}]}


def policy(bucket, principal):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ValueError('invalid_bucket')
    if not re.fullmatch(r'arn:aws:iam::[a-zA-Z0-9-]+:user/[a-zA-Z0-9-]+:[a-zA-Z0-9-]+', principal):
        raise ValueError('invalid_principal')
    resource = 'arn:aws:s3:::' + bucket
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'RBXMemoryBackupBucketRead', 'Effect': 'Allow',
         'Principal': {'AWS': principal}, 'Action': READ_BUCKET, 'Resource': resource},
        {'Sid': 'RBXMemoryBackupObjectRead', 'Effect': 'Allow',
         'Principal': {'AWS': principal}, 'Action': READ_OBJECT, 'Resource': resource + '/*'},
        {'Sid': 'RBXMemoryBackupNoMutation', 'Effect': 'Deny',
         'Principal': {'AWS': principal}, 'NotAction': READ_BUCKET + READ_OBJECT,
         'Resource': [resource, resource + '/*']}]}


def merge(before, addition):
    before = before or {'Version': '2012-10-17', 'Statement': []}
    if before.get('Version') != addition['Version'] or not isinstance(before.get('Statement'), list):
        raise ValueError('source_policy_requires_review')
    owned = {s['Sid'] for s in addition['Statement']}
    if any(s.get('Sid') in owned for s in before['Statement']):
        raise ValueError('owned_policy_statement_already_present')
    return {**before, 'Statement': before['Statement'] + addition['Statement']}
