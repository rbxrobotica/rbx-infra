import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('archive', Path(__file__).with_name('archive.py'))
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class Renderer:
    def parse(self, text):
        return ['document'] if text.strip() else []


class Source:
    def __init__(self):
        self.bodies = {'ops/first.md': b'# First\n\nA real-looking fixture.\n',
                       'agents/second.md': '# Second\n\nMemória sintética.\n'.encode()}
        self.calls = []; self.list_count = 0; self.drift = False

    def list_objects_v2(self, **args):
        self.calls.append(('list_objects_v2', args)); self.list_count += 1
        rows = [{'Key': key, 'Size': len(raw), 'ETag': '"fixture-etag"',
                 'LastModified': '2026-11-15T00:00:00Z'} for key, raw in self.bodies.items()]
        if self.drift and self.list_count > 1:
            rows[0]['ETag'] = '"changed"'
        return {'Contents': rows, 'IsTruncated': False}

    def get_object(self, **args):
        self.calls.append(('get_object', args))
        if args['IfMatch'] != '"fixture-etag"':
            raise AssertionError('missing conditional read')
        return {'Body': io.BytesIO(self.bodies[args['Key']]), 'ETag': '"fixture-etag"',
                'ContentType': 'text/markdown; charset=utf-8', 'Metadata': {'purpose': 'fixture'}}

    def get_object_tagging(self, **args):
        self.calls.append(('get_object_tagging', args))
        return {'TagSet': [{'Key': 'fixture', 'Value': 'true'}]}

    def __getattr__(self, name):
        if name not in archive.READS:
            raise AssertionError('unexpected source mutation or operation')
        def read(**args):
            self.calls.append((name, args)); return {}
        return read


PLAN = {'source_bucket': 'synthetic-private-source', 'source_endpoint': 'https://source.invalid',
        'source_region': 'test', 'dataset_id': 'synthetic-documents', 'max_objects': 10,
        'max_object_bytes': 1024, 'max_total_bytes': 2048, 'minimum_expected_objects': 2,
        'consistency_model': 'independent documents with reconciled conditional reads'}


def rewrite(raw, change):
    with tarfile.open(fileobj=io.BytesIO(raw)) as a:
        items = [(m.name, a.extractfile(m).read()) for m in a.getmembers()]
    items = change(items)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as a:
        for name, body in items:
            info = tarfile.TarInfo(name); info.size = len(body)
            a.addfile(info, io.BytesIO(body))
    return out.getvalue()


class ArchiveControls(unittest.TestCase):
    def capture(self, source=None):
        return archive.collect(source or Source(), PLAN)

    def test_complete_capture_restores_bytes_metadata_and_unicode(self):
        source = Source(); raw, report = self.capture(source)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            restored = archive.restore(raw, report['manifest_sha256'], root, Renderer())
            self.assertEqual(restored['objects_restored'], 2)
            for key, body in source.bodies.items():
                self.assertEqual((root/key).read_bytes(), body)
            sidecar = json.loads((root/'_recovery-metadata.json').read_text())
            self.assertEqual(sidecar['objects'][0]['headers']['Metadata'], {'purpose':'fixture'})
            self.assertEqual(sidecar['objects'][0]['tags'], [{'Key':'fixture','Value':'true'}])
        self.assertTrue(all(name in archive.READS for name, _ in source.calls))
        self.assertFalse(report['atomic_cross_document_snapshot'])

    def test_listing_drift_refuses_capture(self):
        source = Source(); source.drift = True
        with self.assertRaisesRegex(ValueError, 'source_listing_drift'):
            self.capture(source)

    def test_traversal_source_refused(self):
        source = Source(); source.bodies['../secret.md'] = b'# Unsafe'
        with self.assertRaisesRegex(ValueError, 'unsafe_document_path'):
            self.capture(source)
        self.assertFalse(any(name == 'get_object' for name, _ in source.calls))

    def test_size_bound_prevents_body_read(self):
        source = Source(); source.bodies['large.md'] = b'x'*1025
        with self.assertRaisesRegex(ValueError, 'object_size_bound'):
            self.capture(source)

    def test_non_utf8_is_not_a_document(self):
        source = Source(); source.bodies['ops/first.md'] = b'\xff\xfe'
        with self.assertRaises(UnicodeDecodeError):
            self.capture(source)

    def test_untrusted_manifest_never_writes(self):
        raw, _ = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'untrusted_manifest'):
                archive.restore(raw, '0'*64, Path(tmp), Renderer())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_corrupt_payload_never_writes(self):
        raw, report = self.capture()
        bad = rewrite(raw, lambda rows: [(k, b'corrupt' if k.startswith('objects/') else v) for k,v in rows])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'payload_integrity_failed'):
                archive.restore(bad, report['manifest_sha256'], Path(tmp), Renderer())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_member_refused(self):
        raw, report = self.capture(); bad = rewrite(raw, lambda rows: rows[:-1])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'archive_member_set_mismatch'):
                archive.restore(bad, report['manifest_sha256'], Path(tmp), Renderer())

    def test_extra_member_refused(self):
        raw, report = self.capture(); bad = rewrite(raw, lambda rows: rows + [('unexpected',b'bad')])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'archive_member_set_mismatch'):
                archive.restore(bad, report['manifest_sha256'], Path(tmp), Renderer())

    def test_duplicate_member_refused(self):
        raw, report = self.capture(); bad = rewrite(raw, lambda rows: rows + [rows[0]])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'duplicate_or_missing_manifest'):
                archive.restore(bad, report['manifest_sha256'], Path(tmp), Renderer())

    def test_symlink_is_never_extracted(self):
        raw, report = self.capture(); output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as a:
            info = tarfile.TarInfo('manifest.json'); info.type = tarfile.SYMTYPE; info.linkname='/etc/passwd'
            a.addfile(info)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'nonregular_or_large_archive_member'):
                archive.restore(output.getvalue(), report['manifest_sha256'], Path(tmp), Renderer())

    def test_restore_never_overwrites_an_existing_file(self):
        raw, report = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'agents').mkdir(); target=root/'agents/second.md'
            target.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                archive.restore(raw, report['manifest_sha256'], root, Renderer())
            self.assertEqual(target.read_bytes(), b'keep')

    def test_empty_corpus_cannot_pass_as_complete(self):
        source = Source(); source.bodies = {}
        with self.assertRaisesRegex(ValueError, 'unexpectedly_small_corpus'):
            self.capture(source)

    def test_wrong_owner_signature_is_refused(self):
        p = SimpleNamespace(returncode=0, stdout=b'clear fixture', stderr=
            b'[GNUPG:] VALIDSIG BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB 2026-11-15 1 0 4 0 1 10 00 BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n')
        with patch.object(archive.subprocess, 'run', return_value=p):
            with self.assertRaisesRegex(ValueError, 'owner_signature_not_verified'):
                archive.gpg_decrypt(b'fixture', 'A'*40)

    def test_primary_owner_of_signing_subkey_is_accepted(self):
        p = SimpleNamespace(returncode=0, stdout=b'clear fixture', stderr=
            b'[GNUPG:] VALIDSIG BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB 2026-11-15 1 0 4 0 1 10 00 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n')
        with patch.object(archive.subprocess, 'run', return_value=p):
            self.assertEqual(archive.gpg_decrypt(b'fixture', 'A'*40), b'clear fixture')

    def test_symlink_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'file').write_bytes(b'fixture'); (p/'link').symlink_to(p/'file')
            with self.assertRaisesRegex(ValueError, 'unsafe_or_oversized_input'):
                archive.read_bounded(p/'link')

    def test_diagnostics_never_return_arbitrary_error_contents(self):
        detail=archive.safe_error(ValueError('secret-object-name/secret?token=credential'))
        self.assertEqual(detail, {'success':False,'error_type':'ValueError'})
        self.assertEqual(archive.safe_error(ValueError('source_listing_drift'))['error_code'],
                         'source_listing_drift')


if __name__ == '__main__':
    unittest.main()
