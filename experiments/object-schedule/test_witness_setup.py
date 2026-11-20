import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest import mock
import witness_setup


class WitnessOnlyTests(unittest.TestCase):
    def stage(self,root):
        (root/'files').mkdir()
        for name in witness_setup.FILES:(root/'files'/name).write_text('public fixture')
        (root/'files/runtime.json').write_text(json.dumps({'dataset_id':'C008','source_scope_qualified':False,
          'capture_enabled':False,'fresh_restore_qualified':True}))
        for name in ('witness.service','witness.timer'):(root/name).write_text('public fixture')
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


if __name__=='__main__':unittest.main()
