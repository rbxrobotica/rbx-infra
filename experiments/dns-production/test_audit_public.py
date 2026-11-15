"""Adversarial qualification controls; no live DNS or private production keys."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import ed25519
import dns.dnssec
import dns.flags
import dns.message
import dns.rrset

SPEC = importlib.util.spec_from_file_location("public_dns_audit", Path(__file__).with_name("audit_public.py"))
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
ZONE = "initelo.com."
NOW = 1794700000


class PublicAuditControls(unittest.TestCase):
    def setUp(self):
        self.key = ed25519.Ed25519PrivateKey.generate()
        self.dnskey = dns.dnssec.make_dnskey(self.key.public_key(), 15, flags=257)
        self.keys = dns.rrset.from_rdata(ZONE, 300, self.dnskey)
        self.ds = dns.dnssec.make_ds(ZONE, self.dnskey, 2)
        self.soa = dns.rrset.from_text(ZONE, 300, "IN", "SOA",
            "ns1.rbxsystems.ch. hostmaster.rbxsystems.ch. 10 3600 900 604800 300")

    def response(self, data, *, signer=None, key=None, expiration=NOW+7200):
        signature = dns.dnssec.sign(data, signer or self.key, ZONE, key or self.dnskey,
                                    inception=NOW-60, expiration=expiration)
        response = dns.message.make_response(dns.message.make_query(ZONE, data.rdtype))
        response.flags |= dns.flags.AA
        response.answer = [data, dns.rrset.from_rdata(ZONE, 300, signature)]
        return response

    def test_ds_authenticated_keys_validate_soa(self):
        result = AUDIT.validate_authority(self.response(self.soa), self.response(self.keys),
                                          ZONE, [self.ds], now=NOW)
        self.assertEqual(result, 7200)

    def test_untrusted_key_cannot_self_authenticate_even_with_trusted_key_present(self):
        attacker = ed25519.Ed25519PrivateKey.generate()
        key = dns.dnssec.make_dnskey(attacker.public_key(), 15, flags=257)
        keys = dns.rrset.from_rdata(ZONE, 300, self.dnskey, key)
        forged = self.response(keys, signer=attacker, key=key)
        with self.assertRaises(dns.exception.DNSException):
            AUDIT.validate_authority(self.response(self.soa), forged, ZONE, [self.ds], now=NOW)

    def test_wrong_parent_ds_is_rejected(self):
        key = ed25519.Ed25519PrivateKey.generate()
        wrong = dns.dnssec.make_ds(ZONE, dns.dnssec.make_dnskey(key.public_key(), 15, flags=257), 2)
        with self.assertRaisesRegex(ValueError, "no_dnskey_matches"):
            AUDIT.validate_authority(self.response(self.soa), self.response(self.keys),
                                     ZONE, [wrong], now=NOW)

    def test_expired_soa_is_rejected(self):
        with self.assertRaises(dns.exception.DNSException):
            AUDIT.validate_authority(self.response(self.soa, expiration=NOW-1),
                                     self.response(self.keys), ZONE, [self.ds], now=NOW)

    def test_non_authoritative_replay_is_rejected(self):
        response = self.response(self.soa)
        response.flags &= ~dns.flags.AA
        with self.assertRaisesRegex(ValueError, "not_authoritative"):
            AUDIT.validate_authority(response, self.response(self.keys), ZONE, [self.ds], now=NOW)

    def test_missing_dnskey_signature_is_rejected(self):
        response = self.response(self.keys)
        response.answer.pop()
        with self.assertRaisesRegex(ValueError, "missing_or_duplicate"):
            AUDIT.validate_authority(self.response(self.soa), response, ZONE, [self.ds], now=NOW)

    def test_delv_unsigned_is_not_secure_or_accepted_for_signed_intent(self):
        self.assertEqual(AUDIT.delv_verdict(0, "; unsigned answer\n", "", True),
                         "expected_secure_but_insecure")
        self.assertEqual(AUDIT.delv_verdict(0, "; unsigned answer\n", "", False), "insecure")

    def test_delv_failure_or_unknown_output_cannot_be_promoted(self):
        self.assertEqual(AUDIT.delv_verdict(1, "; fully validated\n", "", True), "validation_failed")
        self.assertEqual(AUDIT.delv_verdict(0, "untrusted TXT fully validated", "", True),
                         "unqualified_validator_output")
        self.assertEqual(AUDIT.delv_verdict(0, "; fully validated\n", "", True), "secure")

    def test_wrong_owner_records_are_not_counted_as_delegation(self):
        response = dns.message.make_response(dns.message.make_query(ZONE, "NS"))
        response.authority = [dns.rrset.from_text("attacker.invalid.", 300, "IN", "NS", "ns.bad.invalid.")]
        self.assertEqual(AUDIT.values(response, ZONE, "NS", ("answer", "authority")), [])

    def test_truncated_udp_falls_back_to_tcp_and_truncated_tcp_fails(self):
        truncated = dns.message.make_response(dns.message.make_query(ZONE, "DNSKEY"))
        truncated.flags |= dns.flags.TC
        complete = self.response(self.keys)
        with mock.patch.object(AUDIT.dns.query, "udp", return_value=truncated), \
             mock.patch.object(AUDIT.dns.query, "tcp", return_value=complete):
            response, fallback = AUDIT.ask("192.0.2.1", ZONE, "DNSKEY")
            self.assertTrue(fallback)
            self.assertIs(response, complete)
        with mock.patch.object(AUDIT.dns.query, "udp", return_value=truncated), \
             mock.patch.object(AUDIT.dns.query, "tcp", return_value=truncated):
            with self.assertRaisesRegex(ValueError, "truncated_tcp"):
                AUDIT.ask("192.0.2.1", ZONE, "DNSKEY")

    def test_recursive_udp_timeout_may_use_tcp_but_direct_udp_failure_is_preserved(self):
        response = self.response(self.soa)
        with mock.patch.object(AUDIT.dns.query, "udp", side_effect=dns.exception.Timeout), \
             mock.patch.object(AUDIT.dns.query, "tcp", return_value=response) as tcp:
            self.assertTrue(AUDIT.ask("192.0.2.1", ZONE, "SOA", recursive=True)[1])
            tcp.assert_called_once()
            with self.assertRaises(dns.exception.Timeout):
                AUDIT.ask("192.0.2.1", ZONE, "SOA", recursive=False)


if __name__ == "__main__":
    unittest.main()
