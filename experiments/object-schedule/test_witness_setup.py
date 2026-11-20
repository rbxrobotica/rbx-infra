import hashlib,importlib.util,json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
import witness_setup


class WitnessOnlyTests(unittest.TestCase):
    def stage(self,root):
        (root/'files').mkdir()
        for name in witness_setup.FILES:(root/'files'/name).write_text('public fixture')
        (root/'files/runtime.json').write_text(json.dumps({'dataset_id':'C008','source_scope_qualified':False,
          'capture_enabled':False,'fresh_restore_qualified':True,
          'qualified_run_id':'20261120T120000Z-'+'a'*32,'initial_expected_run_id':'20261120T120000Z-'+'a'*32,
          'qualified_ciphertext_sha256':'b'*64}))
        (root/'witness.service').write_text((witness_setup.HERE/'witness.service').read_text().replace('/opt/rbx-memory-backup','/opt/rbx-memory-witness'))
        (root/'witness.timer').write_bytes((witness_setup.HERE/'witness.timer').read_bytes())
        m={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        raw=json.dumps(m).encode();(root/'manifest.json').write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_stage_cannot_add_source_writer_signer_or_hidden_files(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);sha=self.stage(root)
            witness_setup.validate_stage(root,sha)
            for name in ('source','writer','signer','private-key.asc'):
                p=root/'files'/name;p.write_text('synthetic forbidden material')
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'unexpected_witness_members'):
                    witness_setup.validate_stage(root,sha)
                p.unlink()

    def test_source_or_capture_promotion_and_release_drift_are_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);sha=self.stage(root)
            (root/'files/runtime.py').write_text('changed public fixture')
            with self.assertRaisesRegex(ValueError,'witness_stage_drift'):witness_setup.validate_stage(root,sha)
            with self.assertRaisesRegex(ValueError,'witness_manifest_drift'):witness_setup.validate_stage(root,'0'*64)

    def test_prepare_requires_completed_remote_recovery_before_credentials(self):
        with mock.patch.object(witness_setup.supervised,'trusted_receipt',return_value={'isolated_restore_passed':False}),\
             mock.patch.object(witness_setup.source_access,'stored') as secrets:
            with self.assertRaisesRegex(ValueError,'fresh_recovery_qualification_required'):
                witness_setup.prepare(Path('/unused'),Path('/unused'))
            secrets.assert_not_called()

    def test_nested_manifest_empty_directory_and_fifo_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);sha=self.stage(root)
            for name,kind in (('manifest.json','file'),('unused','dir'),('pipe','fifo')):
                p=root/'files'/name
                if kind=='file':p.write_text('unreviewed nested manifest')
                elif kind=='dir':p.mkdir()
                else:os.mkfifo(p)
                with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'unexpected_witness_members'):
                    witness_setup.validate_stage(root,sha)
                p.rmdir() if kind=='dir' else p.unlink()

    def test_release_carries_real_access_module_import_dependency(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);self.stage(root)
            for name in ('access.py','access-plan.json'):
                (root/'files'/name).write_bytes((witness_setup.HERE/name).read_bytes())
            spec=importlib.util.spec_from_file_location('staged_access_probe',root/'files/access.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertTrue(module.desired('verifier','synthetic-bucket')['capabilities'])
            self.assertIn('access-plan.json',witness_setup.FILES)

    def test_material_projection_drops_unrelated_custody_fields(self):
        key={'applicationKeyId':'synthetic-access','applicationKey':'synthetic-secret',
          **witness_setup.access.desired('verifier','fixture-bucket'),'unexpected_field':'synthetic unrelated'}
        raw=witness_setup.verifier_material(json.dumps(key).encode(),'fixture-bucket')
        self.assertNotIn('unexpected_field',json.loads(raw))
        self.assertEqual(json.loads(raw)['applicationKey'],'synthetic-secret')


if __name__=='__main__':unittest.main()
