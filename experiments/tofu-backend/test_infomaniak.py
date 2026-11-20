"""Offline admission, transport, and engine tests for the native Ceph fixture."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import core
import engine
import infomaniak


RUN_ID = '20261120-0123abcd'
TENANT_ID = 'a1' * 16
ENDPOINT = 'https://s3.gva2.infomaniak.cloud'
BUCKET = f'rbx-tofu-ik-{RUN_ID}'
PREFIX = f'qualification/v2/{RUN_ID}/'
ACCESS_KEY = 'offline-synthetic-access-key'
SECRET_KEY = 'offline-synthetic-secret-key'
PASSPHRASE = 'offline-synthetic-fixture-passphrase'
SSE_KEY = b'\x9c' * 32
SSE_BASE64 = base64.b64encode(SSE_KEY).decode()


def binding(**changes):
    return {
        'run_id': RUN_ID,
        'tenant_id': TENANT_ID,
        'provider': 'infomaniak-native-ceph',
        'region': 'gva2',
        'endpoint': ENDPOINT,
        'project_name': f'rbx-tofu-qual-{RUN_ID}',
        'dedicated_synthetic_project_verified': True,
        'bucket': BUCKET,
        'prefix': PREFIX,
        **changes,
    }


def custody_entries():
    return {
        'runtime': {
            'run_id': RUN_ID, 'tenant_id': TENANT_ID,
            'access_key': ACCESS_KEY, 'secret_key': SECRET_KEY,
        },
        'encryption': {'run_id': RUN_ID, 'passphrase': PASSPHRASE},
        'sse-c': {'run_id': RUN_ID, 'key_base64': SSE_BASE64},
    }


class OfflineTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.base = root / 'owned'
        self.base.mkdir(mode=0o700)
        self.fixture_root = self.base / RUN_ID
        self.enterContext(mock.patch.object(core, 'BASE', self.base))
        self.enterContext(mock.patch.object(core, 'PASS_STORE', root / 'unused-custody'))
        self.enterContext(mock.patch.object(
            socket, 'socket', side_effect=AssertionError('unexpected network socket')
        ))
        self.run_process = self.enterContext(mock.patch.object(
            subprocess, 'run', side_effect=AssertionError('unexpected subprocess')
        ))
        self.launch_process = self.enterContext(mock.patch.object(
            subprocess, 'Popen', side_effect=AssertionError('unexpected process launch')
        ))
        self.values = custody_entries()
        self.read = self.enterContext(mock.patch.object(
            infomaniak, 'read', side_effect=self.values.__getitem__
        ))

    def fake_sdk(self):
        sdk = ModuleType('botocore')
        sdk.session = ModuleType('botocore.session')
        config = ModuleType('botocore.config')
        config.Config = mock.Mock(side_effect=lambda **kwargs: kwargs)
        api = SimpleNamespace(meta=SimpleNamespace(events=mock.Mock()))
        create_client = mock.Mock(return_value=api)
        sdk.session.get_session = mock.Mock(
            return_value=SimpleNamespace(create_client=create_client)
        )
        self.enterContext(mock.patch.dict(sys.modules, {
            'botocore': sdk, 'botocore.session': sdk.session, 'botocore.config': config,
        }))
        self.enterContext(mock.patch.object(sys, 'path', list(sys.path)))
        return api, create_client


class FixtureAdmissionTests(OfflineTest):
    def test_only_exact_native_https_endpoint_matches_signing_region(self):
        _, create_client = self.fake_sdk()
        for region in ('gva1', 'gva2'):
            fixture = binding(region=region, endpoint=f'https://s3.{region}.infomaniak.cloud')
            self.assertIs(infomaniak.admit(fixture), fixture)
        for url in (
            'https://s3.pub1.infomaniak.cloud', 'https://s3.pub2.infomaniak.cloud',
            ENDPOINT.replace('https://', 'http://'),
            ENDPOINT.replace('https://', 'https://user:password@'),
            ENDPOINT.replace('https://', 'https://@'),
            ENDPOINT + '.example.invalid', ENDPOINT + ':444', ENDPOINT + '/foreign',
            ENDPOINT + '?redirect=1', 'https://s3.gva1.infomaniak.cloud',
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, 'unexpected_infomaniak_endpoint'):
                    infomaniak.client(binding(endpoint=url), '/unused-sdk')
        with self.assertRaisesRegex(ValueError, 'unexpected_infomaniak_endpoint'):
            infomaniak.client(binding(region='pub2'), '/unused-sdk')
        with self.assertRaisesRegex(ValueError, 'legacy_or_unknown_provider'):
            infomaniak.client(binding(provider='infomaniak-swift'), '/unused-sdk')
        self.read.assert_not_called()
        create_client.assert_not_called()

    def test_admission_refuses_wrong_run_tenant_project_bucket_or_prefix(self):
        _, create_client = self.fake_sdk()
        failures = (
            ('run_id', '20261119-0123abcd', 'unexpected_infomaniak_run'),
            ('run_id', '20261120-0123abcd/extra', 'unexpected_infomaniak_run'),
            ('tenant_id', 'shared-production', 'invalid_fixture_tenant'),
            ('project_name', 'shared-production', 'dedicated_project_not_verified'),
            ('dedicated_synthetic_project_verified', 1, 'dedicated_project_not_verified'),
            ('bucket', 'production-state', 'unexpected_infomaniak_namespace'),
            ('bucket', 'rbx-tofu-ik-20261120-deadbeef', 'unexpected_infomaniak_namespace'),
            ('prefix', 'production/', 'unexpected_infomaniak_namespace'),
            ('prefix', PREFIX + '../', 'unexpected_infomaniak_namespace'),
        )
        for key, value, error in failures:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, error):
                    infomaniak.client(binding(**{key: value}), '/unused-sdk')
        self.read.assert_not_called()
        create_client.assert_not_called()

    def test_runtime_scope_or_format_drift_fails_before_sdk_creation(self):
        _, create_client = self.fake_sdk()
        for field, value, error in (
            ('tenant_id', 'b2' * 16, 'infomaniak_runtime_scope_mismatch'),
            ('run_id', '20261120-deadbeef', 'infomaniak_runtime_scope_mismatch'),
            ('access_key', 'short', 'invalid_infomaniak_runtime_format'),
            ('secret_key', None, 'invalid_infomaniak_runtime_format'),
        ):
            with self.subTest(field=field):
                self.values['runtime'] = {**custody_entries()['runtime'], field: value}
                with self.assertRaisesRegex(ValueError, error):
                    infomaniak.client(binding(), '/unused-sdk')
                self.read.assert_called_with('runtime')
        create_client.assert_not_called()

    def test_sse_accepts_only_base64_encoded_32_bytes_for_the_fixture(self):
        self.assertEqual(infomaniak.sse_key(binding()), SSE_KEY)
        expected_md5 = base64.b64encode(hashlib.md5(SSE_KEY, usedforsecurity=False).digest()).decode()
        self.assertEqual(infomaniak.sse(binding()), {
            'SSECustomerAlgorithm': 'AES256',
            'SSECustomerKey': SSE_BASE64,
            'SSECustomerKeyMD5': expected_md5,
        })
        for key in ('not-valid-base64!', base64.b64encode(b'x' * 31).decode(),
                    base64.b64encode(b'x' * 33).decode()):
            with self.subTest(key_length=len(key)), self.assertRaises(ValueError):
                self.values['sse-c']['key_base64'] = key
                infomaniak.sse_key(binding())
        self.values['sse-c'] = {**custody_entries()['sse-c'], 'run_id': '20261120-deadbeef'}
        with self.assertRaisesRegex(ValueError, 'sse_key_wrong_fixture'):
            infomaniak.sse_key(binding())
        self.launch_process.assert_not_called()


class EngineFixtureTests(OfflineTest):
    def test_existing_unreviewed_cli_file_is_not_blessed_on_first_configuration(self):
        core.owned_dir(self.fixture_root)
        working=core.owned_dir(self.fixture_root/'a')
        cli=working/'cli.tfrc';cli.write_text('plugin_cache_dir = "/unreviewed"\n')
        with self.assertRaisesRegex(ValueError,'unreviewed_cli_configuration'):
            infomaniak.configure(binding(),self.fixture_root,'a')
        self.assertFalse((working/'fixture-guard.json').exists())
        self.assertIn('/unreviewed',cli.read_text())
        self.launch_process.assert_not_called()
    def test_config_is_sealed_builtin_fixture_with_native_locking_and_no_secrets(self):
        working = infomaniak.configure(binding(), self.fixture_root, 'a')
        engine.admit_working(working)
        raw = (working / 'fixture.tf.json').read_text()
        configuration = json.loads(raw)
        self.assertEqual(set(configuration), {'terraform', 'variable', 'resource', 'output'})
        self.assertEqual(set(configuration['resource']), {'terraform_data'})
        self.assertEqual(set(configuration['resource']['terraform_data']), {'fixture'})
        self.assertEqual(configuration['resource']['terraform_data']['fixture']['provisioner'], [
            {'local-exec': {'command': shlex.join(['/usr/bin/python3', '-B', str(core.HERE / 'hold.py')])}},
        ])
        terraform = configuration['terraform']
        self.assertEqual(set(terraform['backend']), {'s3'})
        backend = terraform['backend']['s3']
        self.assertEqual(backend['endpoints'], {'s3': ENDPOINT})
        self.assertEqual(backend['region'], 'gva2')
        self.assertEqual(backend['bucket'], BUCKET)
        self.assertEqual(backend['key'], PREFIX + 'active/state.tfstate')
        self.assertEqual(backend['workspace_key_prefix'], PREFIX + 'active/workspaces')
        for field in ('use_lockfile', 'use_path_style', 'encrypt'):
            self.assertIs(backend[field], True)
        self.assertEqual(terraform['encryption'], {'state': {'enforced': True}, 'plan': {'enforced': True}})
        self.assertTrue({'access_key', 'secret_key', 'sse_customer_key'}.isdisjoint(backend))
        self.assertTrue(all(secret not in raw for secret in (ACCESS_KEY, SECRET_KEY, PASSPHRASE, SSE_BASE64)))
        self.read.assert_not_called()
        self.launch_process.assert_not_called()

    def test_escape_paths_names_and_unavailable_override_fail_before_launch(self):
        for root, name, unavailable, message in (
            (self.base.parent / 'outside', 'a', False, 'outside_owned_lab_root'),
            (self.fixture_root, '../outside', False, 'unexpected_engine_name'),
            (self.fixture_root, 'a', True, 'unreviewed_infomaniak_endpoint_override'),
        ):
            with self.subTest(name=name, unavailable=unavailable):
                with self.assertRaisesRegex(ValueError, message):
                    infomaniak.configure(binding(), root, name, unavailable=unavailable)
        self.assertFalse((self.base.parent / 'outside').exists())
        self.assertFalse(self.fixture_root.exists())
        self.launch_process.assert_not_called()
        self.read.assert_not_called()

    def test_cloud_env_uses_only_fixture_secrets_and_discards_ambient_injection(self):
        working = infomaniak.configure(binding(), self.fixture_root, 'a')
        ambient = {name: 'injected' for name in (
            'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SSE_CUSTOMER_KEY', 'AWS_PROFILE',
            'AWS_SESSION_TOKEN', 'AWS_ENDPOINT_URL_S3', 'AWS_CA_BUNDLE', 'AWS_CONFIG_FILE',
            'TF_CLI_ARGS', 'TF_CLI_ARGS_apply', 'TF_CLI_CONFIG_FILE', 'TF_DATA_DIR', 'TF_LOG',
            'TF_ENCRYPTION', 'TF_VAR_generation', 'HTTP_PROXY', 'HTTPS_PROXY', 'BASH_ENV',
        )}
        before = (working / 'fixture.tf.json').read_bytes()
        with mock.patch.dict(os.environ, ambient):
            env = infomaniak.environment(binding(), working, 2, hold=True)
        self.assertNotIn('injected', env.values())
        self.assertEqual((env['AWS_ACCESS_KEY_ID'], env['AWS_SECRET_ACCESS_KEY']), (ACCESS_KEY, SECRET_KEY))
        self.assertEqual(env['AWS_SSE_CUSTOMER_KEY'], SSE_BASE64)
        self.assertEqual(env['AWS_SHARED_CREDENTIALS_FILE'], '/dev/null')
        self.assertEqual(env['AWS_CONFIG_FILE'], '/dev/null')
        self.assertEqual(env['TF_CLI_CONFIG_FILE'], str(working / 'cli.tfrc'))
        self.assertEqual(env['TF_DATA_DIR'], str(working / 'data'))
        self.assertEqual((env['TF_VAR_generation'], env['RBX_TOFU_HOLD']), ('2', '1'))
        self.assertIn(PASSPHRASE, env['TF_ENCRYPTION'])
        self.assertEqual((working / 'fixture.tf.json').read_bytes(), before)

    def test_offline_env_does_not_even_request_cloud_credentials_or_sse_key(self):
        working = infomaniak.configure(binding(), self.fixture_root, 'offline', unavailable=True)
        self.values.pop('runtime')
        self.values.pop('sse-c')
        with mock.patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'ambient', 'AWS_SESSION_TOKEN': 'ambient'}):
            env = infomaniak.environment(binding(), working, 1, cloud=False)
        self.read.assert_called_once_with('encryption')
        self.assertTrue({'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                         'AWS_SSE_CUSTOMER_KEY'}.isdisjoint(env))
        self.assertEqual(env['AWS_EC2_METADATA_DISABLED'], 'true')
        self.assertIn(PASSPHRASE, env['TF_ENCRYPTION'])
        configuration = json.loads((working / 'fixture.tf.json').read_text())
        self.assertEqual(configuration['terraform']['backend']['s3']['endpoints'], {'s3': 'https://127.0.0.1:1'})
        self.launch_process.assert_not_called()

    def test_launcher_rejects_unapproved_operation_lock_bypass_and_extra_config(self):
        working = infomaniak.configure(binding(), self.fixture_root, 'a')
        binary = self.base / 'not-executed'
        with mock.patch.object(engine, 'verify_binary'):
            for args, message in (
                (['destroy'], 'engine_operation_not_allowed'),
                (['apply', '-lock=false'], 'unsafe_engine_flag'),
                (['init', '-migrate-state'], 'unsafe_engine_flag'),
                (['init', '-force-copy'], 'unsafe_engine_flag'),
            ):
                with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                    engine.start(binary, working, {}, args)
            (working / 'fixture.tofu.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'unexpected_configuration_or_local_state'):
                engine.start(binary, working, {}, ['plan'])
        self.launch_process.assert_not_called()


class TransportTests(OfflineTest):
    def test_listing_is_bounded_and_never_implicitly_enumerates_the_fixture_bucket(self):
        operation,_,_=self.make_transport()
        for model in ('ListObjectVersions','ListObjectsV2'):
            for extra in ({},{'Prefix':'production/'},{'Prefix':PREFIX+'../'},{'Prefix':PREFIX,'MaxKeys':101}):
                with self.subTest(model=model,extra=extra),self.assertRaisesRegex(ValueError,'unscoped_infomaniak_listing'):
                    operation(model=SimpleNamespace(name=model),params={'Bucket':BUCKET,**extra})
            operation(model=SimpleNamespace(name=model),params={'Bucket':BUCKET,'Prefix':PREFIX,'MaxKeys':100})

    def test_setup_and_upload_cannot_enable_public_access_or_lock_retention(self):
        operation,_,_=self.make_transport('setup')
        ordinary={'Bucket':BUCKET,'CreateBucketConfiguration':{'LocationConstraint':'gva2'}}
        operation(model=SimpleNamespace(name='CreateBucket'),params=ordinary)
        for extra in ({'ACL':'public-read'},{'ObjectLockEnabledForBucket':True}):
            with self.assertRaisesRegex(ValueError,'unreviewed_bucket_creation'):
                operation(model=SimpleNamespace(name='CreateBucket'),params={**ordinary,**extra})
        put={'Bucket':BUCKET,'Key':PREFIX+'probes/canary','Body':b'bounded synthetic canary',**infomaniak.sse(binding())}
        operation(model=SimpleNamespace(name='PutObject'),params=put)
        for extra in ({'ACL':'public-read'},{'ObjectLockMode':'COMPLIANCE'},{'ServerSideEncryption':'AES256'}):
            with self.assertRaisesRegex(ValueError,'unsafe_synthetic_upload'):
                operation(model=SimpleNamespace(name='PutObject'),params={**put,**extra})
    def make_transport(self, mode='runtime'):
        api, create_client = self.fake_sdk()
        budget = infomaniak.Budget()
        self.assertIs(infomaniak.client(binding(), '/unused-sdk', budget, mode=mode), api)
        events = dict(call.args for call in api.meta.events.register.call_args_list)
        self.assertEqual(set(events), {'before-parameter-build.s3', 'before-send.s3'})
        settings = create_client.call_args.kwargs
        self.assertEqual(settings['endpoint_url'], ENDPOINT)
        self.assertEqual(settings['region_name'], 'gva2')
        self.assertIs(settings['verify'], True)
        self.assertEqual(settings['config']['signature_version'], 's3v4')
        self.assertEqual(settings['config']['s3'], {'addressing_style': 'path'})
        return events['before-parameter-build.s3'], events['before-send.s3'], budget

    def test_send_hook_rejects_redirect_other_bucket_and_escaped_prefix_before_budget(self):
        _, send, budget = self.make_transport()
        for url, error in (
            ('https://s3.gva1.infomaniak.cloud/' + BUCKET, 'infomaniak_request_destination_refused'),
            (ENDPOINT.replace('https://', 'http://') + '/' + BUCKET, 'infomaniak_request_destination_refused'),
            (ENDPOINT.replace('https://', 'https://user@') + '/' + BUCKET, 'infomaniak_request_destination_refused'),
            (ENDPOINT + f'/{BUCKET}?X-Amz-Credential=synthetic', 'infomaniak_request_destination_refused'),
            (ENDPOINT + f'/{BUCKET}-escape/{PREFIX}object', 'other_infomaniak_bucket_refused'),
            (ENDPOINT + f'/{TENANT_ID}%3A{BUCKET}/{PREFIX}object', 'other_infomaniak_bucket_refused'),
            (ENDPOINT + f'/{BUCKET}/production/state.tfstate', 'other_infomaniak_prefix_refused'),
            (ENDPOINT + f'/{BUCKET}/{PREFIX}%2E%2E/foreign', 'ambiguous_infomaniak_path'),
            (ENDPOINT + f'/{BUCKET}?policy', 'unreviewed_infomaniak_subresource'),
            (ENDPOINT + f'/{BUCKET}?delete', 'unreviewed_infomaniak_subresource'),
            (ENDPOINT + f'/{BUCKET}/qualification/v2/{RUN_ID}-escape/value', 'other_infomaniak_prefix_refused'),
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, error):
                send(SimpleNamespace(url=url, method='PUT', headers={'Content-Length': '5'}))
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (0, 0, 0))
        send(SimpleNamespace(url=ENDPOINT + f'/{BUCKET}/{PREFIX}active/state.tfstate',
                             method='GET', headers={}))
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (1, 0, 0))

    def test_send_hook_refuses_one_byte_beyond_the_upload_budget(self):
        _, send, budget = self.make_transport()
        url = ENDPOINT + f'/{BUCKET}/{PREFIX}active/state.tfstate'
        send(SimpleNamespace(url=url, method='PUT', headers={'Content-Length': str(1 << 20)}))
        with self.assertRaisesRegex(ValueError, 'synthetic_request_budget_exceeded'):
            send(SimpleNamespace(url=url, method='POST', headers={'Content-Length': '1'}))
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (0, 1, 1 << 20))

    def test_unknown_or_streaming_uploads_fail_before_send(self):
        operation,send,budget=self.make_transport()
        put={'Bucket':BUCKET,'Key':PREFIX+'probes/canary',**infomaniak.sse(binding())}
        for extra in ({},{'Body':iter([b'chunk'])},{'Body':b'x'*(1048576+1)},
                      {'Body':b'bounded','ContentLength':0}):
            with self.subTest(extra_keys=list(extra)),self.assertRaisesRegex(ValueError,'unbounded_synthetic_upload'):
                operation(model=SimpleNamespace(name='PutObject'),params={**put,**extra})
        with self.assertRaisesRegex(ValueError,'unknown_synthetic_content_length'):
            send(SimpleNamespace(url=ENDPOINT+f'/{BUCKET}/{PREFIX}probes/canary',method='PUT',headers={}))
        self.assertEqual((budget.reads,budget.mutating,budget.uploaded_bytes),(0,0,0))

    def test_operation_guard_refuses_mutations_and_scope_escapes_before_send(self):
        operation, _, budget = self.make_transport()
        state = PREFIX + 'active/state.tfstate'
        lock = state + '.tflock'
        options = {'Bucket': BUCKET}
        blocked = (
            ('DeleteBucket', options, 'unreviewed_infomaniak_operation'),
            ('PutBucketPolicy', options, 'unreviewed_infomaniak_operation'),
            ('DeleteObjects', options, 'unreviewed_infomaniak_operation'),
            ('CreateBucket', options, 'unreviewed_infomaniak_operation'),
            ('PutBucketVersioning', options, 'unreviewed_infomaniak_operation'),
            ('PutObject', {'Bucket': 'foreign', 'Key': state}, 'other_infomaniak_bucket_refused'),
            ('PutObject', {**options, 'Key': 'foreign/object'}, 'other_infomaniak_prefix_refused'),
            ('PutObject', {**options, 'Key': PREFIX + '../object'}, 'other_infomaniak_prefix_refused'),
            ('PutObject', {**options, 'Key': PREFIX + '/object'}, 'other_infomaniak_prefix_refused'),
            ('DeleteObject', {**options, 'Key': state}, 'synthetic_delete_refused'),
            ('DeleteObject', {**options, 'Key': lock, 'VersionId': 'v1'}, 'synthetic_delete_refused'),
        )
        for name, params, message in blocked:
            with self.subTest(name=name, params=params), self.assertRaisesRegex(ValueError, message):
                operation(model=SimpleNamespace(name=name), params=params)
        for key in (lock, PREFIX + 'probes/conditional-write'):
            operation(model=SimpleNamespace(name='DeleteObject'), params={**options, 'Key': key})
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (0, 0, 0))

    def test_setup_can_enable_fixture_versioning_and_rejects_suspension(self):
        operation, _, _ = self.make_transport('setup')
        model = SimpleNamespace(name='PutBucketVersioning')
        operation(model=model, params={'Bucket': BUCKET, 'VersioningConfiguration': {'Status': 'Enabled'}})
        with self.assertRaisesRegex(ValueError, 'versioning_downgrade_refused'):
            operation(model=model, params={'Bucket': BUCKET, 'VersioningConfiguration': {'Status': 'Suspended'}})
        with self.assertRaisesRegex(ValueError, 'other_infomaniak_bucket_refused'):
            operation(model=model, params={'Bucket': 'foreign', 'VersioningConfiguration': {'Status': 'Enabled'}})

    def test_budget_rejects_negative_fractional_boolean_sizes_and_unreviewed_methods(self):
        budget = infomaniak.Budget()
        for method, size in (('PUT', -1), ('POST', 1.5), ('PUT', True), ('PATCH', 0)):
            with self.subTest(method=method, size=size):
                with self.assertRaisesRegex(ValueError, 'invalid_synthetic_request_budget'):
                    budget.reserve(method, size)
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (0, 0, 0))

    def test_exact_read_and_write_operation_limits_are_independent(self):
        budget = infomaniak.Budget()
        for _ in range(500):
            budget.reserve('GET')
            budget.reserve('HEAD')
        for _ in range(100):
            budget.reserve('PUT')
            budget.reserve('DELETE')
        for method in ('GET', 'HEAD', 'PUT', 'POST', 'DELETE'):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'synthetic_request_budget_exceeded'):
                budget.reserve(method)
        self.assertEqual((budget.reads, budget.mutating, budget.uploaded_bytes), (1000, 200, 0))


if __name__ == '__main__':
    unittest.main()
