#!/usr/bin/env python3
"""Inspect or apply the reviewed institutional Postmark callback cutover.

Default is read-only. No email is sent. Values stay in memory, and only sanitized
operation metadata is emitted. Apply only after the GitOps edge/app gates pass.
"""
import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER_ID = 19089132
OLD_HOST = 'comms.rbxsystems.ch'
NEW_HOST = 'api.comms.rbxsystems.ch'
HOOKS = {24453184: ('bounce', 'Bounce'), 24453185: ('bounce', 'SpamComplaint'),
         24453186: ('delivery', 'Delivery')}


class CutoverError(Exception):
    """Value-free operator error."""


def secret(entry):
    try:
        result = subprocess.run(['pass', 'show', entry], capture_output=True, timeout=30, check=True)
        return result.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise CutoverError('Could not open the required encrypted credential') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Provider:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request('https://api.postmarkapp.com' + path, data=data,
                                     method='GET' if body is None else 'PUT', headers={
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-Postmark-Server-Token': self.token})
        try:
            with self.opener.open(req, timeout=30) as response:
                return json.load(response)
        except (OSError, ValueError):
            # A timeout may follow a successful write. Do not replay or roll
            # back blindly; rerun inspection and reconcile exact configuration.
            raise CutoverError('Provider request failed or outcome unknown; inspect before retrying') from None


def destination(path, credential=None):
    authority = NEW_HOST
    if credential:
        authority = (urllib.parse.quote(credential['username'], safe='') + ':' +
                     urllib.parse.quote(credential['password'], safe='') + '@' + authority)
    return 'https://' + authority + '/api/webhooks/postmark/' + path


def check_location(value, path, credential=None):
    u = urllib.parse.urlsplit(value)
    if u.scheme != 'https' or u.hostname not in (OLD_HOST, NEW_HOST) or u.port is not None:
        raise CutoverError('Callback origin drifted from the reviewed scope')
    if u.path != '/api/webhooks/postmark/' + path or u.query or u.fragment:
        raise CutoverError('Callback path drifted from the reviewed scope')
    if u.username or u.password:
        if (credential is None or u.hostname != NEW_HOST or
                urllib.parse.unquote(u.username or '') != credential['username'] or
                urllib.parse.unquote(u.password or '') != credential['password']):
            raise CutoverError('Existing URL credential requires coordinated review')


def plans(server, hooks, credential=None):
    if server.get('ID') != SERVER_ID or set(h['ID'] for h in hooks) != set(HOOKS):
        raise CutoverError('Server or webhook inventory drifted from the reviewed scope')
    check_location(server.get('InboundHookUrl', ''), 'inbound', credential)
    result = []
    for hook in hooks:
        path, trigger = HOOKS[hook['ID']]
        check_location(hook.get('Url', ''), path)
        if hook.get('MessageStream') != 'outbound':
            raise CutoverError('Webhook stream drifted from the reviewed scope')
        triggers = hook.get('Triggers', {})
        if {name for name, value in triggers.items() if value.get('Enabled')} != {trigger}:
            raise CutoverError('Webhook triggers drifted from the reviewed scope')
        auth = hook.get('HttpAuth') or {}
        if auth.get('Username') or auth.get('Password'):
            if credential is None or auth != {'Username': credential['username'], 'Password': credential['password']}:
                raise CutoverError('Existing webhook credential requires coordinated review')
        # This inspected server has no custom headers. Stop on any change rather
        # than deleting or copying an unknown authentication mechanism.
        if hook.get('HttpHeaders'):
            raise CutoverError('Webhook headers changed; fresh review required')
        body = {'Url': destination(path), 'HttpHeaders': [], 'Triggers': triggers,
                'Verify': True}
        if credential:
            body['HttpAuth'] = {'Username': credential['username'], 'Password': credential['password']}
        result.append((hook['ID'], body))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--inspect-auth', action='store_true', help='read and reconcile the dedicated credential without writing to the provider')
    args = parser.parse_args(argv)
    try:
        provider = Provider(secret('rbx/postmark/rbx-institutional-server-token').splitlines()[0])
        credential = None
        if args.apply or args.inspect_auth:
            credential = json.loads(secret('rbx/comms/postmark-webhook-auth'))
            if set(credential) != {'username', 'password'} or not all(isinstance(v, str) and v for v in credential.values()):
                raise CutoverError('Invalid dedicated credential bundle')
        server = provider.request('/server')
        hooks = provider.request('/webhooks').get('Webhooks', [])
        updates = plans(server, hooks, credential)
        if args.apply:
            for hook_id, body in updates:
                provider.request('/webhooks/' + str(hook_id), body)
            provider.request('/server', {'InboundHookUrl': destination('inbound', credential)})
            current_server = provider.request('/server')
            current_hooks = provider.request('/webhooks').get('Webhooks', [])
            plans(current_server, current_hooks, credential)
            if current_server.get('InboundHookUrl') != destination('inbound', credential):
                raise CutoverError('Inbound readback did not confirm the intended configuration')
            for hook in current_hooks:
                expected = dict(updates)[hook['ID']]
                if any(hook.get(k) != expected[k] for k in ('Url', 'HttpAuth', 'HttpHeaders', 'Triggers')):
                    raise CutoverError('Webhook readback did not confirm the intended configuration')
        print(json.dumps({'server_id': SERVER_ID, 'webhook_ids': sorted(HOOKS),
                          'target_host': NEW_HOST, 'mode': 'applied-and-readback-verified' if args.apply else 'read-only-plan',
                          'email_sent': False,
                          'observed_webhooks': [{'id': h['ID'], 'target_host_configured': urllib.parse.urlsplit(h['Url']).hostname == NEW_HOST,
                                                 'dedicated_auth_configured': bool((h.get('HttpAuth') or {}).get('Username'))}
                                                for h in (current_hooks if args.apply else hooks)]}))
        return 0
    except CutoverError as exc:
        print(str(exc), file=sys.stderr)
    except (KeyError, TypeError, ValueError):
        print('Unexpected provider or credential shape; diagnostics suppressed', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
