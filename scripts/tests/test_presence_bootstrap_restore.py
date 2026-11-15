"""Bootstrap capture must stop before opening nonempty application data."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from presence import bootstrap_restore_drill as tool


class Commands:
    def __init__(self,counts=(b'0',b'0'),fail_restore=False,container='a'*64):
        self.counts=iter(counts)
        self.fail_restore=fail_restore
        self.container=container
        self.calls=[]
        self.bundle=None

    def __call__(self,argv,*,label,data=None,timeout=120):
        self.calls.append((argv,label))
        if label=='source_metadata': return next(self.counts)
        if label=='source_snapshot':
            assert argv[0]=='ssh' and '--dbname=public_presence' in argv[-1]
            assert 'default_transaction_read_only=on' in argv[-1]
            return b'PGDMPsynthetic-bootstrap'
        if label=='encryption':
            self.bundle=data
            return b'synthetic-ciphertext'
        if label=='decryption': return self.bundle
        if label=='isolated_container':
            assert '--network' in argv and argv[argv.index('--network')+1]=='none'
            assert not any(x in argv for x in ('-v','--volume','--mount','--env','-e'))
            return self.container.encode()
        if label=='isolated_restore' and self.fail_restore: raise tool.DrillError('isolated_restore_failed')
        if label=='restored_metadata': return b'0'
        return b''


class BootstrapRestoreTests(unittest.TestCase):
    def test_ssh_option_like_targets_are_rejected_before_any_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tool,'run') as commands:
            for target in ('-oProxyCommand=x@host','root@-bad','root@host other'):
                with self.subTest(target=target), self.assertRaises(tool.DrillError):
                    tool.drill(target,'A'*40,directory)
            commands.assert_not_called()

    def invoke(self,commands,directory):
        with patch.object(tool,'run',commands), patch.object(tool.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
            return tool.drill('fixture@host.invalid','A'*40,directory)

    def test_nonempty_or_changed_source_stops_before_decryption_and_container(self):
        for counts in ((b'1',b'0'),(b'0',b'1'),(b'unknown',b'0')):
            with self.subTest(counts=counts), tempfile.TemporaryDirectory() as directory:
                commands=Commands(counts)
                with self.assertRaises(tool.DrillError): self.invoke(commands,directory)
                labels=[label for _,label in commands.calls]
                self.assertNotIn('encryption',labels)
                self.assertNotIn('isolated_container',labels)

    def test_restore_uses_only_created_container_and_does_not_claim_external_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            commands=Commands()
            report=self.invoke(commands,directory)
            self.assertFalse(report['external_backup_verified'])
            self.assertFalse(report['production_mutated'])
            self.assertFalse(report['source_credentials_or_private_keys_mounted'])
            self.assertTrue(report['isolated_container_removed'])
            cleanup=[argv for argv,label in commands.calls if label=='isolated_cleanup']
            self.assertEqual(cleanup,[['podman','rm','--force','a'*64]])
            self.assertTrue(all(p.stat().st_mode & 0o077==0 for p in Path(directory).iterdir()))

    def test_failed_restore_cleans_its_container_without_a_passing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            commands=Commands(fail_restore=True)
            with self.assertRaises(tool.DrillError): self.invoke(commands,directory)
            self.assertIn('isolated_cleanup',[label for _,label in commands.calls])
            self.assertFalse(list(Path(directory).glob('*.report.json')))

    def test_unknown_container_identity_never_becomes_a_cleanup_target(self):
        with tempfile.TemporaryDirectory() as directory:
            commands=Commands(container='untrusted-output')
            with self.assertRaises(tool.DrillError): self.invoke(commands,directory)
            self.assertNotIn('isolated_cleanup',[label for _,label in commands.calls])


if __name__=='__main__':
    unittest.main()
