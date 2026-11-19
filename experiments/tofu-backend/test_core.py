"""Offline guard tests for the synthetic OpenTofu backend fixture."""

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    'tofu_qualification_core_under_test', Path(__file__).with_name('core.py')
)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)

RUN_ID = '20261119-0123abcd'
ENDPOINT = 'https://s3.us-test-007.backblazeb2.com'
PREFIX = f'qualification/v1/{RUN_ID}/'
SECRET = 'synthetic-sentinel-secret-42'
READ_CAPABILITIES = ['listBuckets', 'readBuckets', 'listFiles', 'readFiles']
ROLE_SCOPES = {
    'runtime': (READ_CAPABILITIES + ['writeFiles'], ['fixture-state-id'], PREFIX + 'active/'),
    'reader': (READ_CAPABILITIES, ['fixture-state-id'], PREFIX + 'active/'),
    'archive-reader': (
        READ_CAPABILITIES + ['readFileRetentions'], ['fixture-archive-id'], PREFIX + 'snapshots/'
    ),
    'setup-lab': (
        READ_CAPABILITIES + ['writeFiles', 'deleteFiles', 'readFileRetentions'],
        ['fixture-state-id', 'fixture-archive-id'],
        PREFIX,
    ),
}


def fixture_binding():
    return {
        'run_id': RUN_ID,
        'prefix': PREFIX,
        'endpoint': ENDPOINT,
        'buckets': {
            role: {'bucketId': f'fixture-{role}-id', 'bucketName': f'rbx-tofu-{role}-{RUN_ID}'}
            for role in ('state', 'archive')
        },
    }


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(
            subprocess, 'Popen', side_effect=AssertionError('unexpected subprocess')
        ))
        self.enterContext(mock.patch.object(
            socket, 'socket', side_effect=AssertionError('unexpected network socket')
        ))


class AdmissionTests(OfflineTest):
    def test_endpoint_accepts_https_provider_and_normalizes_root_slash(self):
        self.assertEqual(CORE.endpoint(ENDPOINT + '/'), ENDPOINT)
        self.assertEqual(CORE.endpoint(ENDPOINT + ':443'), ENDPOINT + ':443')

    def test_endpoint_rejects_insecure_or_nonfixture_destinations(self):
        for endpoint in (
            ENDPOINT.replace('https://', 'http://'),
            ENDPOINT + ':444',
            'https://s3.us-east-1.amazonaws.com',
            ENDPOINT + '.example.invalid',
            ENDPOINT.replace('https://', 'https://fixture:userinfo@'),
            ENDPOINT + '?redirect=example.invalid',
            ENDPOINT + '#fragment',
            ENDPOINT + '/existing-bucket',
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, 'unexpected_tls_s3_endpoint'):
                    CORE.endpoint(endpoint)

    def test_admit_returns_exact_fixture_binding(self):
        binding = fixture_binding()
        self.assertIs(CORE.admit(binding), binding)

    def test_admit_rejects_unexpected_run_and_prefix(self):
        for field, value, error in (
            ('run_id', '20261118-0123abcd', 'unexpected_run_id'),
            ('run_id', '20261119-0123ABCD', 'unexpected_run_id'),
            ('prefix', 'platform/production/state/', 'unexpected_fixture_prefix'),
            ('prefix', PREFIX + '../', 'unexpected_fixture_prefix'),
            ('prefix', PREFIX.rstrip('/'), 'unexpected_fixture_prefix'),
            ('endpoint', 'https://storage.example.invalid', 'unexpected_tls_s3_endpoint'),
        ):
            with self.subTest(field=field, value=value):
                binding = fixture_binding()
                binding[field] = value
                with self.assertRaisesRegex(ValueError, error):
                    CORE.admit(binding)

    def test_admit_rejects_unexpected_bucket_names_and_missing_ids(self):
        for role in ('state', 'archive'):
            for field, value, error in (
                ('bucketName', 'rbx-production-state', 'unexpected_fixture_bucket'),
                ('bucketName', f'rbx-tofu-{role}-20261119-deadbeef', 'unexpected_fixture_bucket'),
                ('bucketId', '', 'missing_fixture_bucket_id'),
            ):
                with self.subTest(role=role, field=field, value=value):
                    binding = fixture_binding()
                    binding['buckets'][role][field] = value
                    with self.assertRaisesRegex(ValueError, error):
                        CORE.admit(binding)

    def test_admit_rejects_state_and_archive_sharing_a_bucket_id(self):
        binding = fixture_binding()
        binding['buckets']['archive']['bucketId'] = binding['buckets']['state']['bucketId']
        with self.assertRaisesRegex(ValueError, 'same_backend_and_archive_bucket'):
            CORE.admit(binding)


class RuntimeTests(OfflineTest):
    def setUp(self):
        super().setUp()
        sdk = ModuleType('botocore')
        sdk.session = ModuleType('botocore.session')
        config = ModuleType('botocore.config')
        config.Config = mock.Mock()
        self.create_client = mock.Mock()
        sdk.session.get_session = mock.Mock(
            return_value=SimpleNamespace(create_client=self.create_client)
        )
        self.enterContext(mock.patch.dict(sys.modules, {
            'botocore': sdk, 'botocore.session': sdk.session, 'botocore.config': config
        }))
        self.enterContext(mock.patch.object(sys, 'path', list(sys.path)))
        self.stored = self.enterContext(mock.patch.object(CORE, 'stored'))

    def call_client(self, role, key):
        self.stored.return_value = json.dumps({
            'applicationKeyId': 'synthetic-id', 'applicationKey': SECRET, **key
        }).encode()
        return CORE.client(fixture_binding(), role, '/synthetic-sdk-not-used')

    def test_exact_capabilities_and_scope_for_every_role(self):
        self.assertEqual(set(CORE.CAPS), set(ROLE_SCOPES))
        for role, (capabilities, buckets, prefix) in ROLE_SCOPES.items():
            with self.subTest(role=role):
                self.assertCountEqual(CORE.CAPS[role], capabilities)
                self.assertEqual(CORE.scope(fixture_binding(), role), (buckets, prefix))
                client = self.call_client(role, {
                    'capabilities': list(reversed(capabilities)),
                    'bucketIds': buckets,
                    'namePrefix': prefix,
                })
                self.assertIs(client, self.create_client.return_value)
                self.stored.assert_called_with(f'rbx/tofu-backend/{role}', True)
                self.assertEqual(self.create_client.call_args.args, ('s3',))
                self.assertEqual(self.create_client.call_args.kwargs['endpoint_url'], ENDPOINT)
                self.assertIs(self.create_client.call_args.kwargs['verify'], True)
        self.assertEqual(self.create_client.call_count, len(ROLE_SCOPES))

    def test_scope_drift_is_refused_before_client_creation(self):
        for role, (capabilities, buckets, prefix) in ROLE_SCOPES.items():
            expected = {'capabilities': capabilities, 'bucketIds': buckets, 'namePrefix': prefix}
            for field, changed in (
                ('capabilities', capabilities + ['writeKeys']),
                ('capabilities', capabilities[:-1]),
                ('bucketIds', buckets + ['unrelated-bucket-id']),
                ('bucketIds', buckets[:-1]),
                ('bucketIds', ['unrelated-bucket-id', *buckets[1:]]),
                ('namePrefix', prefix + 'other/'),
                ('namePrefix', 'platform/production/'),
            ):
                with self.subTest(role=role, field=field, changed=changed):
                    with self.assertRaisesRegex(ValueError, 'runtime_identity_scope_drift'):
                        self.call_client(role, {**expected, field: changed})
        self.create_client.assert_not_called()

    def test_unknown_role_is_refused_before_custody_lookup(self):
        with self.assertRaisesRegex(ValueError, 'unexpected_runtime_role'):
            self.call_client('admin', {})
        self.stored.assert_not_called()
        self.create_client.assert_not_called()

    def test_send_hook_allows_only_the_configured_https_destination(self):
        capabilities, buckets, prefix = ROLE_SCOPES['reader']
        api = self.call_client('reader', {
            'capabilities': capabilities, 'bucketIds': buckets, 'namePrefix': prefix
        })
        event, check_destination = api.meta.events.register.call_args.args
        self.assertEqual(event, 'before-send.s3')
        check_destination(SimpleNamespace(url=ENDPOINT + '/synthetic-key'))
        for url in (
            ENDPOINT.replace('https://', 'http://') + '/synthetic-key',
            'https://s3.us-other-007.backblazeb2.com/synthetic-key',
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, 'redirect_or_provider_destination_refused'):
                    check_destination(SimpleNamespace(url=url))


class PrivatePathTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.base = self.root / 'lab'
        self.base.mkdir(mode=0o700)
        self.enterContext(mock.patch.object(CORE, 'BASE', self.base))

    def test_owned_dir_creates_private_directory(self):
        destination = self.base / 'fixture'
        self.assertEqual(CORE.owned_dir(destination), destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)

    def test_owned_dir_refuses_outside_root_paths(self):
        destination = self.root / 'outside'
        for path in (destination, self.base, Path('relative/fixture'), self.base / '..' / 'outside'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, 'outside_owned_lab_root'):
                    CORE.owned_dir(path)
        self.assertFalse(destination.exists())

    def test_owned_dir_refuses_symlink_and_group_permissions(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        link = self.base / 'symlink'
        link.symlink_to(target, target_is_directory=True)
        for path in (link, link / 'child'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, 'outside_owned_lab_root'):
                    CORE.owned_dir(path)
        self.assertFalse((target / 'child').exists())

        public = self.base / 'group-readable'
        public.mkdir(mode=0o700)
        public.chmod(0o750)
        with self.assertRaisesRegex(ValueError, 'unsafe_lab_directory'):
            CORE.owned_dir(public)


class CustodyTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.pass_store = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'custody'
        self.enterContext(mock.patch.object(CORE, 'PASS_STORE', self.pass_store))
        self.run = self.enterContext(mock.patch.object(subprocess, 'run'))

    def mark_existing(self, name):
        target = self.pass_store / f'rbx/tofu-backend/{name}.gpg'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()

    def result(self, returncode=0, value=None):
        stdout = json.dumps(value).encode() if value is not None else b''
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=b'')

    def assert_commands(self, commands):
        self.assertEqual([call.args[0][:2] for call in self.run.call_args_list], commands)
        for call in self.run.call_args_list:
            if call.args[0][0] == 'pass':
                self.assertEqual(call.kwargs['env']['PASSWORD_STORE_DIR'], str(self.pass_store))

    def test_existing_custody_overwrite_refused_for_key_or_other_run(self):
        for name, previous_run in (('runtime', RUN_ID), ('fixture', '20261119-deadbeef')):
            with self.subTest(name=name):
                self.mark_existing(name)
                self.run.reset_mock()
                self.run.return_value = self.result(value={'run_id': previous_run})
                with self.assertRaisesRegex(ValueError, 'existing_lab_custody_requires_review'):
                    CORE.store(name, {'run_id': RUN_ID})
                self.assert_commands([['pass', 'show']])

    def test_same_run_fixture_update_requires_readback_and_checkpoint(self):
        self.mark_existing('fixture')
        previous = {'run_id': RUN_ID, 'setup_complete': False}
        replacement = {'run_id': RUN_ID, 'setup_complete': True}
        self.run.side_effect = [self.result(value=previous), self.result(),
                                self.result(value=replacement), self.result()]
        CORE.store('fixture', replacement)
        self.assert_commands([['pass', 'show'], ['pass', 'insert'], ['pass', 'show'], ['git', '-C']])
        insert = self.run.call_args_list[1]
        self.assertEqual(insert.args[0][-1], 'rbx/tofu-backend/fixture')
        self.assertEqual(json.loads(insert.kwargs['input']), replacement)
        self.assertEqual(self.run.call_args.args[0], [
            'git', '-C', str(self.pass_store), 'push', 'origin',
            'HEAD:refs/heads/codex/synthetic-tofu-backend-20261119',
        ])

    def test_readback_mismatch_prevents_checkpoint(self):
        self.run.side_effect = [self.result(), self.result(value={'run_id': 'wrong'})]
        with self.assertRaisesRegex(ValueError, 'custody_readback_mismatch'):
            CORE.store('fixture', {'run_id': RUN_ID})
        self.assert_commands([['pass', 'insert'], ['pass', 'show']])

    def test_private_branch_push_failure_prevents_success(self):
        value = {'run_id': RUN_ID}
        self.run.side_effect = [self.result(), self.result(value=value), self.result(returncode=1)]
        with self.assertRaisesRegex(ValueError, 'private_custody_checkpoint_failed'):
            CORE.store('fixture', value)
        self.assert_commands([['pass', 'insert'], ['pass', 'show'], ['git', '-C']])


class SafeCallTests(OfflineTest):
    def test_sdk_exception_exposes_only_status_and_safe_error_code(self):
        for code, expected in (('AccessDenied', 'AccessDenied'), (SECRET, 'provider_error')):
            with self.subTest(code=code):
                error = RuntimeError(f'SDK error contains {SECRET}')
                error.response = {
                    'Error': {'Code': code, 'Message': f'request echoed {SECRET}'},
                    'ResponseMetadata': {'HTTPStatusCode': 403, 'RequestId': SECRET},
                }
                result = CORE.safe_call(mock.Mock(side_effect=error))
                self.assertEqual(result, ({'ok': False, 'http': 403, 'error_code': expected}, None))
                self.assertNotIn(SECRET, repr(result))

    def test_generic_exception_exposes_only_exception_class(self):
        result = CORE.safe_call(mock.Mock(side_effect=RuntimeError(SECRET)))
        self.assertEqual(result, ({'ok': False, 'http': None, 'error_code': 'RuntimeError'}, None))
        self.assertNotIn(SECRET, repr(result))


if __name__ == '__main__':
    unittest.main()
