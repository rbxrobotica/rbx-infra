import json,tempfile,unittest
from pathlib import Path
import core,storage_controls


class RecoveryAdmission(unittest.TestCase):
    def test_receipt_integrity_and_failed_control_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'receipt.json';binding={'run_id':'20261119-aaaaaaaa'}
            report={'type':'local HTTPS calibration, not provider qualification',
                    'run_id':binding['run_id'],'completed':True,'all_executed_gates_pass':True}
            path.write_text(json.dumps(report));sha=core.digest(path.read_bytes())
            self.assertEqual(storage_controls.checked_receipt(path,sha,binding),report)
            with self.assertRaisesRegex(ValueError,'untrusted'):storage_controls.checked_receipt(path,'0'*64,binding)
            report['all_executed_gates_pass']=False;path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'incomplete'):storage_controls.checked_receipt(path,core.digest(path.read_bytes()),binding)
    def test_envelope_screen_does_not_accept_plaintext_or_public_canary(self):
        self.assertFalse(storage_controls.encrypted(b'{"resources":[]}'))
        self.assertFalse(storage_controls.encrypted(b'{"encrypted_data":"RBX_SYNTHETIC_TOFU_CANARY_V1"}'))
        # Screening is not decryption validation; real OpenTofu show performs that gate.
        self.assertTrue(storage_controls.encrypted(b'{"encrypted_data":"synthetic-envelope-only"}'))


if __name__=='__main__':unittest.main()
