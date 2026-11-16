import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comms.backup_runner import Config, BackupError
from plausible.s3_archive import validate_key, ArchiveError


class FlightDeckBackupScope(unittest.TestCase):
    def test_scope_cannot_redirect_other_apps_or_admit_another_database(self):
        with tempfile.TemporaryDirectory() as directory:
            public_key=Path(directory)/'recipient.asc'
            public_key.write_text('test fixture, not a real key')
            env={'DATABASE_URL':'postgres://reader:fixture@localhost/flightdeck',
                 'S3_ARCHIVE_ENDPOINT':'https://example.com','S3_ARCHIVE_BUCKET':'test-bucket',
                 'AWS_ACCESS_KEY_ID':'fixture','AWS_SECRET_ACCESS_KEY':'fixture',
                 'BACKUP_RECIPIENT_FINGERPRINT':'1'*40,'BACKUP_PUBLIC_KEY_FILE':str(public_key)}
            config=Config(env,scope='flightdeck')
            self.assertEqual(config.database,'flightdeck')
            self.assertEqual(config.pg_env['PGOPTIONS'],'-c default_transaction_read_only=on')
            self.assertEqual(config.archive_script.parent.name,'flightdeck')
            with self.assertRaises(BackupError): Config(env,scope='comms')
            with self.assertRaises(BackupError): Config({**env,'DATABASE_URL':'postgres://reader:fixture@localhost/rbx_comms'},scope='flightdeck')
            with self.assertRaises(BackupError): Config({**env,'AWS_ACCESS_KEY_ID':''},scope='flightdeck')

    def test_archive_collection_is_separate(self):
        self.assertEqual(validate_key('flightdeck/backups/full/2026-11-16/test.gpg',scope='flightdeck'),
                         'flightdeck/backups/full/2026-11-16/test.gpg')
        for scope in ('comms','plausible','public-presence'):
            with self.assertRaises(ArchiveError): validate_key('flightdeck/backups/full/test.gpg',scope=scope)
        with self.assertRaises(ArchiveError): validate_key('comms/backups/full/test.gpg',scope='flightdeck')


if __name__ == '__main__': unittest.main()
