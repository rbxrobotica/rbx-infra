import io
import unittest
from unittest.mock import patch
import zipfile

import rehearsal as lab


class ArchiveContract(unittest.TestCase):
    def setUp(self):
        self.members = {name: name.encode() for name in lab.MEMBERS}
        self.hashes = {name: lab.metadata(body) for name, body in self.members.items()}
        self.archive = lab.package(self.members)

    def test_archive_members_reconcile(self):
        self.assertEqual(lab.unpack(self.archive, self.hashes), self.members)

    def test_missing_manifest_and_trusted_member_hash_fail(self):
        del self.hashes["manifest.json"]
        with self.assertRaisesRegex(lab.LabError, "trusted member"):
            lab.unpack(self.archive, self.hashes)

    def test_duplicate_or_unexpected_members_fail(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, body in self.members.items():
                archive.writestr(name, body)
            archive.writestr("../escape", b"x")
        with self.assertRaisesRegex(lab.LabError, "structure"):
            lab.unpack(output.getvalue(), self.hashes)

    def test_wrong_member_bytes_and_truncated_zip_fail(self):
        self.hashes["roles.sql"] = lab.metadata(b"incorrect")
        with self.assertRaisesRegex(lab.LabError, "roles.sql"):
            lab.unpack(self.archive, self.hashes)
        with self.assertRaisesRegex(lab.LabError, "invalid closed ZIP"):
            lab.unpack(self.archive[:-5], self.hashes)

    @patch("rehearsal.gpg")
    def test_untrusted_receipt_fails_before_decrypt(self, gpg):
        with self.assertRaisesRegex(lab.LabError, "trusted receipt hash"):
            lab.read_verified(b"x", b"{}", "0" * 64, None, None)
        gpg.assert_not_called()

    @patch("rehearsal.gpg")
    def test_corrupted_ciphertext_fails_before_decrypt(self, gpg):
        receipt = lab.canonical({"members": self.hashes, "ciphertext": lab.metadata(b"x")})
        with self.assertRaisesRegex(lab.LabError, "ciphertext"):
            lab.read_verified(b"y", receipt, lab.digest(receipt), None, None)
        gpg.assert_not_called()


class EndpointContract(unittest.TestCase):
    @patch("rehearsal.command")
    def test_identity_failure_prevents_exec_and_removal(self, command):
        cluster = lab.Cluster("a" * 16, "target")
        cluster.cid = "b" * 64
        command.return_value.stdout = b"wrong identity"
        with self.assertRaisesRegex(lab.LabError, "identity"):
            cluster.exec("psql")
        with self.assertRaisesRegex(lab.LabError, "identity"):
            cluster.remove()
        self.assertEqual([call.args[0][len(lab.PODMAN)] for call in command.call_args_list],
                         ["inspect", "inspect"])

    def test_database_system_id_mismatch_prevents_mutation(self):
        cluster = lab.Cluster("a" * 16, "target")
        cluster.system_id = "123"
        with patch.object(cluster, "psql") as psql:
            psql.return_value.stdout = b"postgres|lab_admin|160015|/lab/db|/lab/socket||t|55416|456\n"
            with self.assertRaisesRegex(lab.LabError, "endpoint identity"):
                cluster.mutate("postgres", b"CREATE ROLE x")
            self.assertEqual(psql.call_count, 1)

    def test_database_role_mismatch_prevents_mutation(self):
        cluster = lab.Cluster("a" * 16, "target")
        with patch.object(cluster, "check_endpoint"), patch.object(cluster, "psql") as psql:
            psql.return_value.stdout = b"lab_fixture|lab_admin|t\n"
            with self.assertRaisesRegex(lab.LabError, "role identity"):
                cluster.mutate("lab_fixture", b"CREATE TABLE x()", "lab_reader")
            self.assertEqual(psql.call_count, 1)

    def test_connection_options_are_explicit_and_environment_is_cleared(self):
        self.assertEqual(lab.Cluster.endpoint("lab_fixture", "lab_reader"),
                         ("--host=/lab/socket", "--port=55416",
                          "--username=lab_reader", "--dbname=lab_fixture"))
        self.assertEqual(lab.DB_ENV[:2], ("/usr/bin/env", "-i"))
        self.assertIn("--remote=false", lab.PODMAN)


if __name__ == "__main__":
    unittest.main()
