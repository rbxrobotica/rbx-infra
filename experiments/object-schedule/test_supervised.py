import copy,importlib.util,json,socket,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import supervised


def custody():
    return {'dataset_id':'rbx-memory-documents-first-capture','source':{'source_bucket':'rbx-memory',
      'source_endpoint':'https://eu2.contabostorage.com','source_region':'default',
      'source_access_ref':'rbx/s3/access-key','source_secret_ref':'rbx/s3/secret-key'}}


class SupervisedGuardTests(unittest.TestCase):
    def test_owner_mapping_cannot_be_redirected_or_expanded(self):
        self.assertEqual(supervised.plan(custody())['max_objects'],100)
        for field,value in [('source_bucket','other'),('source_endpoint','http://eu2.contabostorage.com'),
                            ('source_access_ref','other/credential')]:
            c=copy.deepcopy(custody());c['source'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):supervised.plan(c)
        c=custody();c['dataset_id']='other'
        with self.assertRaises(ValueError):supervised.plan(c)

    def test_source_hooks_refuse_other_corpora_mutations_and_redirects(self):
        for name in ('PutObject','DeleteObject','ListBuckets','PutBucketPolicy'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                supervised.source_guard(SimpleNamespace(name=name),{'Bucket':'rbx-memory'})
        with self.assertRaises(ValueError):
            supervised.source_guard(SimpleNamespace(name='GetObject'),{'Bucket':'other'})
        supervised.source_guard(SimpleNamespace(name='GetObject'),{'Bucket':'rbx-memory'})
        for url,method in [('http://eu2.contabostorage.com/rbx-memory/a.md','GET'),
                           ('https://other.invalid/rbx-memory/a.md','GET'),
                           ('https://eu2.contabostorage.com/other/a.md','GET'),
                           ('https://eu2.contabostorage.com/rbx-memory/a.md','PUT')]:
            with self.subTest(url=url,method=method),self.assertRaises(ValueError):
                supervised.source_destination(SimpleNamespace(url=url,method=method))

    def test_importing_module_does_not_capture_or_publish(self):
        with mock.patch.object(supervised.source_access,'stored') as secret,\
             mock.patch.object(supervised.subprocess,'run') as process:
            spec=importlib.util.spec_from_file_location('not_a_worker',Path(supervised.__file__))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            secret.assert_not_called();process.assert_not_called()

    def test_sdk_manifest_drift_fails_before_import(self):
        with tempfile.TemporaryDirectory() as temp,mock.patch.object(supervised.sys,'path',list(supervised.sys.path)):
            stage=Path(temp);(stage/'manifest.json').write_text('{}')
            before=list(supervised.sys.path)
            with self.assertRaisesRegex(ValueError,'untrusted_sdk_manifest'):supervised.verified_sdk(stage)
            self.assertEqual(supervised.sys.path,before)

    def test_capture_requires_explicit_supervised_mode_before_secrets_or_network(self):
        with mock.patch.object(supervised.source_access,'stored') as secret:
            with self.assertRaisesRegex(ValueError,'supervised_read_not_selected'):
                supervised.capture(SimpleNamespace(allow_supervised_owner_read=False))
            secret.assert_not_called()


class OfflinePublishTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.git = self.enterContext(mock.patch.object(
            supervised.subprocess, 'run', side_effect=AssertionError('unexpected subprocess')
        ))
        self.enterContext(mock.patch.object(
            supervised.subprocess, 'Popen', side_effect=AssertionError('unexpected process launch')
        ))
        self.enterContext(mock.patch.object(
            socket, 'socket', side_effect=AssertionError('unexpected network socket')
        ))
        self.custody = self.enterContext(mock.patch.object(
            supervised.source_access, 'stored', side_effect=AssertionError('unexpected custody read')
        ))


class TrustedReceiptTests(OfflinePublishTest):
    def setUp(self):
        super().setUp()
        self.repo = self.root / 'approved-checkout'
        self.path = self.repo / 'evidence/2026-11-20-memory-refresh/capture-1.json'
        self.path.parent.mkdir(parents=True)
        self.raw = b'{"dataset_id":"C008"}'
        self.path.write_bytes(self.raw)
        self.enterContext(mock.patch.object(supervised, 'EVIDENCE', self.repo))
        self.revision = 'a' * 40
        self.origin = 'https://github.com/rbxrobotica/rbx-resilience.git'
        self.remote_ref = 'refs/heads/' + supervised.BRANCH
        self.advertised = self.revision + '\t' + self.remote_ref
        self.pinned = self.raw
        self.reported_repo = self.repo
        self.git.side_effect = self.fake_git

    def fake_git(self, argv, **kwargs):
        expected = {
            ('rev-parse', '--show-toplevel'): str(self.reported_repo),
            ('branch', '--show-current'): supervised.BRANCH,
            ('remote', 'get-url', 'origin'): self.origin,
            ('rev-parse', 'refs/remotes/origin/' + supervised.BRANCH): self.revision,
            ('ls-remote', 'origin', self.remote_ref): self.advertised,
            ('show', self.revision + ':' + str(self.path.relative_to(self.repo))): self.pinned,
        }
        self.assertEqual(argv[:2], ['git', '-C'])
        self.assertTrue(kwargs['check'])
        return subprocess.CompletedProcess(argv, 0, stdout=expected[tuple(argv[3:])])

    def test_matching_advertised_commit_and_exact_blob_admit_receipt(self):
        self.assertEqual(supervised.trusted_receipt(self.path), {'dataset_id': 'C008'})
        self.assertEqual(self.git.call_args_list[-2].args[0][3:], ['ls-remote', 'origin', self.remote_ref])
        self.assertEqual(self.git.call_args.args[0][3], 'show')

    def test_symlink_and_foreign_evidence_path_are_refused_before_git(self):
        link = self.path.with_name('capture-link.json')
        link.symlink_to(self.path)
        for path in (link, self.root / 'foreign/capture.json'):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'unexpected_evidence_path'):
                supervised.trusted_receipt(path)
        self.git.assert_not_called()

    def test_foreign_origin_is_refused_before_remote_lookup(self):
        self.origin = 'https://github.com/other/rbx-resilience.git'
        with self.assertRaisesRegex(ValueError, 'unexpected_evidence_origin'):
            supervised.trusted_receipt(self.path)
        self.assertEqual(self.git.call_args.args[0][3:], ['remote', 'get-url', 'origin'])

    def test_foreign_repo_is_refused_even_with_expected_receipt_path(self):
        self.reported_repo = self.root / 'other'
        with self.assertRaisesRegex(ValueError, 'unexpected_evidence_repository'):
            supervised.trusted_receipt(self.path)
        self.git.assert_called_once()

    def test_cached_commit_without_matching_remote_advertisement_is_refused(self):
        for advertised in ('', 'b' * 40 + '\t' + self.remote_ref):
            with self.subTest(advertised=advertised):
                self.git.reset_mock()
                self.advertised = advertised
                with self.assertRaisesRegex(ValueError, 'evidence_remote_not_acknowledged'):
                    supervised.trusted_receipt(self.path)
                self.assertEqual(self.git.call_args.args[0][3:], ['ls-remote', 'origin', self.remote_ref])

    def test_changed_local_bytes_are_refused_after_remote_commit_check(self):
        self.pinned = b'{"dataset_id":"C008","different":true}'
        with self.assertRaisesRegex(ValueError, 'receipt_not_remotely_checkpointed'):
            supervised.trusted_receipt(self.path)
        self.assertEqual(self.git.call_args.args[0][3], 'show')


class PublishBindingTests(OfflinePublishTest):
    def setUp(self):
        super().setUp()
        self.now = 1795176000
        self.run_id = '20261120T120000Z-' + 'a' * 32
        self.prefix = supervised.runtime.PREFIX + self.run_id + '/'
        self.bucket = 'fixture-bucket-id'
        self.cipher = b'synthetic-cipher-placeholder'
        self.report = {
            'dataset_id': 'C008', 'run_id': self.run_id,
            'started_epoch': self.now - 30, 'finished_epoch': self.now - 15,
            'ciphertext_sha256': supervised.runtime.digest(self.cipher),
            'ciphertext_bytes': len(self.cipher), 'manifest_sha256': 'b' * 64,
            'objects': 31, 'source_bytes': 123, 'atomic_cross_document_snapshot': False,
            'fresh_source_capture': True, 'source_mutations': 0,
        }
        base = self.root / 'private'
        self.enterContext(mock.patch.object(supervised, 'BASE', base))
        self.work = supervised.private_dir(base / 'capture', new=True)
        supervised.write_new(self.work / 'archive.tar.gpg', self.cipher)
        self.args = SimpleNamespace(capture_receipt=self.root / 'receipt.json', work_dir=self.work)
        self.anchor = self.enterContext(mock.patch.object(supervised, 'trusted_receipt', return_value=self.report))
        fixture_custody = {**custody(), 'destination': {'bucket_id': self.bucket}}
        values = {
            'rbx/object-backup/memory-custody': fixture_custody,
            'rbx/object-backup/b2-writer': {'fixture_role': 'writer'},
            'rbx/object-backup/b2-verifier': {'fixture_role': 'verifier'},
        }
        self.custody.side_effect = lambda name: json.dumps(values[name]).encode()
        secret = bytes(range(32))
        self.public = supervised.Ed25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
            supervised.serialization.Encoding.Raw, supervised.serialization.PublicFormat.Raw
        )
        self.enterContext(mock.patch.object(supervised, 'signer', return_value=(secret, self.public)))
        self.enterContext(mock.patch.object(supervised.runtime, 'clock', return_value=self.now))
        self.writer = mock.Mock()
        self.verifier = mock.Mock()
        self.clients = self.enterContext(mock.patch.object(supervised.runtime, 'B2', side_effect=[self.writer, self.verifier]))
        self.witness = self.enterContext(mock.patch.object(
            supervised.runtime, 'witness', return_value={'run_id': self.run_id, 'rotation_due': False}
        ))
        self.prior = {'files': []}
        self.files = {}
        self.metadata_edits = {}
        self.readback_edits = {}
        self.writer.upload.side_effect = self.upload
        self.verifier.api.side_effect = self.verifier_api
        self.verifier.download_file.side_effect = self.download

    def upload(self, name, raw):
        self.assertTrue((self.work / 'publish-started.json').is_file())
        file_id = {'archive.tar.gpg': 'new-cipher-id', 'receipt.json': 'new-receipt-id'}[name.removeprefix(self.prefix)]
        self.assertNotIn(file_id, self.files)
        self.files[file_id] = (name, raw)
        return {'fileId': file_id}

    def verifier_api(self, operation, payload):
        if operation == 'b2_list_file_versions':
            self.assertEqual(payload, {'bucketId': self.bucket, 'prefix': self.prefix, 'maxFileCount': 10})
            return self.prior
        self.assertEqual(operation, 'b2_get_file_info')
        file_id = payload['fileId']
        name, raw = self.files[file_id]
        return {
            'fileName': name, 'contentLength': len(raw),
            'fileRetention': {'isClientAuthorizedToRead': True, 'value': {
                'mode': 'compliance', 'retainUntilTimestamp': (self.now + 7 * 86400) * 1000,
            }},
            **self.metadata_edits.get(file_id, {}),
        }

    def download(self, file_id):
        return self.readback_edits.get(file_id, self.files[file_id][1])

    def test_untrusted_receipt_or_cipher_never_accesses_custody_or_provider(self):
        self.anchor.side_effect = ValueError('receipt_not_remotely_checkpointed')
        with self.assertRaisesRegex(ValueError, 'receipt_not_remotely_checkpointed'):
            supervised.publish(self.args)
        self.anchor.side_effect = None
        self.report['ciphertext_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'untrusted_capture_cipher'):
            supervised.publish(self.args)
        self.custody.assert_not_called()
        self.clients.assert_not_called()
        self.assertFalse((self.work / 'publish-started.json').exists())

    def test_existing_local_claim_stops_before_listing_or_upload(self):
        supervised.write_new(self.work / 'publish-started.json', b'preserve this claim')
        with self.assertRaises(FileExistsError):
            supervised.publish(self.args)
        self.assertEqual((self.work / 'publish-started.json').read_bytes(), b'preserve this claim')
        self.verifier.api.assert_not_called()
        self.writer.upload.assert_not_called()

    def test_remote_partial_attempt_stops_before_upload_and_preserves_claim(self):
        self.prior = {'files': [{'fileName': self.prefix + 'archive.tar.gpg', 'fileId': 'previous-cipher-id'}]}
        with self.assertRaisesRegex(ValueError, 'preserve_existing_remote_attempt'):
            supervised.publish(self.args)
        self.writer.upload.assert_not_called()
        self.witness.assert_not_called()
        claim = json.loads((self.work / 'publish-started.json').read_bytes())
        self.assertEqual(claim['run_id'], self.run_id)
        self.assertEqual(claim['ciphertext_sha256'], self.report['ciphertext_sha256'])

    def test_ambiguous_first_upload_cannot_be_retried(self):
        self.writer.upload.side_effect = TimeoutError('synthetic lost upload response')
        with self.assertRaises(TimeoutError):
            supervised.publish(self.args)
        self.clients.side_effect = [self.writer, self.verifier]
        with self.assertRaises(FileExistsError):
            supervised.publish(self.args)
        self.writer.upload.assert_called_once_with(self.prefix + 'archive.tar.gpg', self.cipher)
        self.verifier.api.assert_called_once()
        self.witness.assert_not_called()

    def test_cipher_identity_mismatch_blocks_witness_and_readback(self):
        self.metadata_edits['new-cipher-id'] = {'fileName': self.prefix + 'foreign.tar.gpg'}
        with self.assertRaisesRegex(ValueError, 'published_version_identity_mismatch'):
            supervised.publish(self.args)
        self.assertEqual(self.verifier.api.call_args.args, ('b2_get_file_info', {'fileId': 'new-cipher-id'}))
        self.witness.assert_not_called()
        self.verifier.download_file.assert_not_called()

    def test_receipt_retention_mismatch_blocks_success_after_exact_id_lookup(self):
        self.metadata_edits['new-receipt-id'] = {'fileRetention': {
            'isClientAuthorizedToRead': True, 'value': {
                'mode': 'governance', 'retainUntilTimestamp': (self.now + 7 * 86400) * 1000,
            },
        }}
        with self.assertRaisesRegex(ValueError, 'compliance_retention_not_effective'):
            supervised.publish(self.args)
        self.assertEqual(self.verifier.api.call_args_list[-2:], [
            mock.call('b2_get_file_info', {'fileId': 'new-cipher-id'}),
            mock.call('b2_get_file_info', {'fileId': 'new-receipt-id'}),
        ])
        self.witness.assert_not_called()
        self.verifier.download_file.assert_not_called()

    def test_exact_receipt_readback_mismatch_blocks_local_recovery_artifacts(self):
        self.readback_edits['new-receipt-id'] = b'not the signed receipt'
        with self.assertRaisesRegex(ValueError, 'independent_readback_mismatch'):
            supervised.publish(self.args)
        self.assertEqual(self.verifier.download_file.call_args_list, [
            mock.call('new-cipher-id'), mock.call('new-receipt-id'),
        ])
        self.assertFalse((self.work / 'downloaded.tar.gpg').exists())
        self.assertFalse((self.work / 'downloaded-receipt.json').exists())

    def test_matching_exact_versions_yield_signed_recovery_pair(self):
        observed = supervised.publish(self.args)
        self.assertTrue(observed['published'])
        self.assertTrue(observed['independent_readback_passed'])
        self.assertTrue(observed['compliance_retention_verified'])
        self.assertEqual((self.work / 'downloaded.tar.gpg').read_bytes(), self.cipher)
        closed = (self.work / 'downloaded-receipt.json').read_bytes()
        receipt = supervised.runtime.verify_receipt(closed, self.public, self.now)
        self.assertEqual(receipt['cipher_file_id'], 'new-cipher-id')
        self.assertEqual(receipt['cipher_name'], self.prefix + 'archive.tar.gpg')
        self.assertEqual(receipt['ciphertext_sha256'], self.report['ciphertext_sha256'])
        self.assertEqual(receipt['finished_epoch'], self.report['finished_epoch'])
        self.assertEqual(self.writer.upload.call_args_list[-1], mock.call(self.prefix + 'receipt.json', closed))
        self.custody.assert_has_calls([mock.call(ref) for ref in (
            'rbx/object-backup/memory-custody', 'rbx/object-backup/b2-writer', 'rbx/object-backup/b2-verifier',
        )])
        self.assertEqual(self.custody.call_count, 3)


class SourceTransportTests(unittest.TestCase):
    def test_dot_segments_duplicate_separators_and_foreign_queries_are_refused(self):
        endpoint = 'https://eu2.contabostorage.com'
        for path in ('/rbx-memory/../other/a.md', '/rbx-memory/%2E%2E/other/a.md',
                     '/rbx-memory/folder/./a.md', '/rbx-memory//a.md',
                     '/rbx-memory/a.md#fragment', '/rbx-memory?delete'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'supervised_source_destination_refused'):
                supervised.source_destination(SimpleNamespace(url=endpoint + path, method='GET'))
        for path in ('/rbx-memory/folder/a.md?tagging=', '/rbx-memory?list-type=2&max-keys=100'):
            supervised.source_destination(SimpleNamespace(url=endpoint + path, method='GET'))


if __name__=='__main__':unittest.main()
