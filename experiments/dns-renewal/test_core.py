"""Offline renewal policy controls; disposable test private keys stay in memory."""

import hashlib
import importlib.util
import json
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

import dns.dnssec
import dns.exception
import dns.rdata
import dns.rdatatype
import dns.zone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


ROOT = pathlib.Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = load_module('renewal_core_under_test', ROOT / 'core.py')
BUILDER = load_module('renewal_public_fixture_builder',
                      ROOT.parent / 'dns-origin/build-fixtures.py')
NOW = 1794620000
SERIAL = 2026111302


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


class RenewalPolicyControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = ed25519.Ed25519PrivateKey.generate()
        cls.dnskey = dns.dnssec.make_dnskey(cls.key.public_key(), 15, flags=257)
        cls.baseline = cls.signed()

    @classmethod
    def signed(cls, serial=SERIAL, inception=NOW-60,
               expiration=NOW+CORE.LIFETIME):
        zone = dns.zone.from_text(BUILDER.unsigned(serial, 'constant-test-data'),
                                  origin=CORE.ORIGIN, relativize=False)
        dns.dnssec.sign_zone(zone, keys=[(cls.key, cls.dnskey)],
                             inception=inception, expiration=expiration)
        return zone

    def clone(self):
        return CORE.parse(self.baseline.to_text(relativize=False,
                                               sorted=True).encode('ascii'))

    def test_full_authoritative_inventory_validates(self):
        checked = CORE.validate(self.baseline, self.baseline, now=NOW)
        self.assertEqual(checked['signed_rrsets_validated'], 24)
        self.assertEqual(checked['serial'], SERIAL)
        self.assertEqual(checked['valid_until_epoch'], NOW+CORE.LIFETIME)

    def test_unsigned_rdata_change_is_rejected(self):
        zone = self.clone()
        data = zone.get_rdataset(CORE.ORIGIN, dns.rdatatype.A)
        data.clear()
        data.add(dns.rdata.from_text('IN', 'A', '192.0.2.99'), data.ttl)
        with self.assertRaisesRegex(ValueError, 'content_or_denial'):
            CORE.validate(zone, self.baseline, now=NOW)

    def test_ttl_change_is_rejected(self):
        zone = self.clone()
        zone.get_rdataset(CORE.ORIGIN, dns.rdatatype.TXT).ttl += 1
        with self.assertRaisesRegex(ValueError, 'content_or_denial'):
            CORE.validate(zone, self.baseline, now=NOW)

    def test_nsec_link_change_is_rejected(self):
        zone = self.clone()
        data = zone.get_rdataset(CORE.ORIGIN, dns.rdatatype.NSEC)
        self.assertIsNotNone(data)
        data.clear()
        data.add(dns.rdata.from_text('IN', 'NSEC',
                                    'unexpected.'+CORE.ORIGIN+' A'), data.ttl)
        with self.assertRaisesRegex(ValueError, 'content_or_denial'):
            CORE.validate(zone, self.baseline, now=NOW)

    def test_removing_any_of_24_signatures_is_rejected(self):
        cases = [(owner, data.rdtype)
                 for owner, node in self.baseline.nodes.items()
                 for data in node.rdatasets
                 if data.rdtype != dns.rdatatype.RRSIG and
                 self.baseline.get_rdataset(owner, dns.rdatatype.RRSIG,
                                            covers=data.rdtype) is not None]
        self.assertEqual(len(cases), 24)
        for owner, kind in cases:
            with self.subTest(owner=owner.to_text(), kind=kind):
                zone = self.clone()
                zone.delete_rdataset(owner, dns.rdatatype.RRSIG, covers=kind)
                with self.assertRaisesRegex(ValueError, 'missing_signature'):
                    CORE.validate(zone, self.baseline, now=NOW)

    def test_forging_any_of_24_signatures_is_rejected(self):
        cases = [(owner, data.covers)
                 for owner, node in self.baseline.nodes.items()
                 for data in node.rdatasets
                 if data.rdtype == dns.rdatatype.RRSIG]
        self.assertEqual(len(cases), 24)
        for owner, kind in cases:
            with self.subTest(owner=owner.to_text(), kind=kind):
                zone = self.clone()
                data = zone.get_rdataset(owner, dns.rdatatype.RRSIG, covers=kind)
                sig = next(iter(data))
                altered = bytes([sig.signature[0] ^ 1]) + sig.signature[1:]
                data.clear()
                data.add(sig.replace(signature=altered), data.ttl)
                with self.assertRaises(dns.exception.DNSException):
                    CORE.validate(zone, self.baseline, now=NOW)

    def test_signature_ttl_change_is_rejected(self):
        zone = self.clone()
        zone.get_rdataset(CORE.ORIGIN, dns.rdatatype.RRSIG,
                          covers=dns.rdatatype.SOA).ttl += 1
        with self.assertRaisesRegex(ValueError, 'signature_set'):
            CORE.validate(zone, self.baseline, now=NOW)

    def test_expired_future_and_low_margin_are_rejected(self):
        cases = [
            ('expired', NOW-CORE.LIFETIME, NOW-1, 0),
            ('future', NOW+1, NOW+CORE.LIFETIME, 0),
            ('margin_equal', NOW-60, NOW+3600, 3600),
            ('margin_short', NOW-60, NOW+3599, 3600),
        ]
        for name, inception, expiration, margin in cases:
            with self.subTest(name=name):
                zone = self.signed(inception=inception, expiration=expiration)
                with self.assertRaisesRegex(ValueError, 'time_or_margin'):
                    CORE.validate(zone, self.baseline, now=NOW,
                                  minimum_remaining=margin)

    def test_margin_one_second_above_requirement_is_valid(self):
        zone = self.signed(expiration=NOW+3601)
        self.assertEqual(CORE.validate(zone, self.baseline, now=NOW,
                                       minimum_remaining=3600)
                         ['signed_rrsets_validated'], 24)

    def test_excessive_lifetime_is_rejected_when_policy_is_required(self):
        zone = self.signed(expiration=NOW+CORE.LIFETIME+121)
        with self.assertRaisesRegex(ValueError, 'lifetime_exceeds_policy'):
            CORE.validate(zone, self.baseline, now=NOW, maximum_remaining=CORE.LIFETIME+120)

    def test_renewal_advances_serial_without_unsigned_changes(self):
        raw, publication = CORE.renew(pem(self.key), self.baseline, SERIAL,
                                      now=NOW+600)
        zone = CORE.parse(raw)
        self.assertEqual(CORE.serial(zone), SERIAL+1)
        self.assertEqual(CORE.semantics(zone), CORE.semantics(self.baseline))
        self.assertEqual(publication['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(publication['valid_until_epoch'], NOW+600+CORE.LIFETIME)
        self.assertEqual(publication['signed_rrsets_validated'], 24)
        self.assertTrue(publication['synthetic_only'])
        self.assertEqual(CORE.validate(zone, self.baseline, now=NOW+600,
                                       floor=SERIAL)['serial'], SERIAL+1)

    def test_renewal_wraps_uint32_serial(self):
        baseline = self.signed(serial=0xffffffff)
        raw, publication = CORE.renew(pem(self.key), baseline, 0xffffffff,
                                      now=NOW)
        self.assertEqual(publication['serial'], 0)
        self.assertEqual(CORE.validate(CORE.parse(raw), baseline, now=NOW,
                                       floor=0xffffffff)['serial'], 0)

    def test_wrong_signer_and_algorithm_are_rejected(self):
        wrong = ed25519.Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError, 'key_anchor_mismatch'):
            CORE.renew(pem(wrong), self.baseline, SERIAL, now=NOW)
        wrong_algorithm = ec.generate_private_key(ec.SECP256R1())
        with self.assertRaisesRegex(ValueError, 'key_algorithm'):
            CORE.renew(pem(wrong_algorithm), self.baseline, SERIAL, now=NOW)

    def test_rfc1982_equal_half_range_and_wrap(self):
        self.assertFalse(CORE.newer(SERIAL, SERIAL))
        self.assertFalse(CORE.newer(SERIAL-1, SERIAL))
        self.assertFalse(CORE.newer(0x80000000, 0))
        self.assertFalse(CORE.newer(0, 0x80000000))
        self.assertTrue(CORE.newer(0, 0xffffffff))
        self.assertFalse(CORE.newer(0xffffffff, 0))
        self.assertEqual(CORE.next_serial(0xffffffff), 0)

    def test_equal_floor_validates_but_older_and_ambiguous_fail(self):
        self.assertEqual(CORE.validate(self.baseline, self.baseline,
                                       now=NOW, floor=SERIAL)['serial'], SERIAL)
        for floor in [SERIAL+1, (SERIAL+0x80000000) & 0xffffffff]:
            with self.subTest(floor=floor):
                with self.assertRaisesRegex(ValueError, 'rollback_or_ambiguous|floor_below_or_ambiguous'):
                    CORE.validate(self.baseline, self.baseline,
                                  now=NOW, floor=floor)

    def test_older_explicit_floor_cannot_override_pinned_baseline(self):
        with self.assertRaisesRegex(ValueError, 'floor_below_or_ambiguous'):
            CORE.validate(self.baseline, self.baseline,
                          now=NOW, floor=SERIAL-1)

    def test_parse_rejects_empty_oversized_and_include_input(self):
        for raw in [b'', b'x'*(CORE.MAX_ZONE+1),
                    b'$INCLUDE /not-a-test-file\n']:
            with self.subTest(length=len(raw)):
                with self.assertRaises((ValueError, dns.exception.DNSException)):
                    CORE.parse(raw)

    def test_policy_rejects_scope_digest_and_anchor_changes(self):
        raw = self.baseline.to_text(relativize=False, sorted=True).encode('ascii')
        ds = dns.dnssec.make_ds(self.baseline.origin, self.dnskey, 'SHA256').to_text()
        config = {'origin': CORE.ORIGIN, 'synthetic_only': True,
                  'baseline_sha256': hashlib.sha256(raw).hexdigest(),
                  'ds_sha256': ds}
        with tempfile.TemporaryDirectory() as work:
            directory = pathlib.Path(work)
            (directory/'baseline.zone').write_bytes(raw)
            (directory/'policy.json').write_text(json.dumps(config))
            self.assertEqual(CORE.policy(directory)[0], config)
            for field, value, error in [
                ('origin', 'unrelated.example.', 'scope'),
                ('synthetic_only', False, 'scope'),
                ('baseline_sha256', '0'*64, 'baseline_hash'),
                ('ds_sha256', ds[:-1]+('0' if ds[-1] != '0' else '1'), 'baseline_anchor'),
            ]:
                with self.subTest(field=field):
                    (directory/'policy.json').write_text(json.dumps({**config, field:value}))
                    with self.assertRaisesRegex(ValueError, error):
                        CORE.policy(directory)


class AtomicPublicStateControls(unittest.TestCase):
    def test_public_snapshot_is_created_and_replaced_whole(self):
        with tempfile.TemporaryDirectory() as work:
            target = pathlib.Path(work)/'current.zone'
            CORE.atomic(target, b'old public snapshot\n')
            CORE.atomic(target, b'new public snapshot\n')
            self.assertEqual(target.read_bytes(), b'new public snapshot\n')
            self.assertEqual(set(pathlib.Path(work).iterdir()), {target})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode) & 0o022, 0)

    def test_foreign_staging_file_does_not_block_or_get_overwritten(self):
        with tempfile.TemporaryDirectory() as work:
            target = pathlib.Path(work)/'current.zone'
            target.write_bytes(b'old')
            staging = pathlib.Path(work)/'current.zone.new'
            staging.write_bytes(b'other operation')
            CORE.atomic(target, b'candidate')
            self.assertEqual(target.read_bytes(), b'candidate')
            self.assertEqual(staging.read_bytes(), b'other operation')
            self.assertEqual(set(pathlib.Path(work).iterdir()), {target, staging})

    def test_staging_symlink_cannot_overwrite_another_file(self):
        with tempfile.TemporaryDirectory() as work:
            root = pathlib.Path(work)
            target, other = root/'current.zone', root/'unrelated-public-data'
            target.write_bytes(b'old')
            other.write_bytes(b'preserve')
            staging = root/'current.zone.new'
            staging.symlink_to(other)
            CORE.atomic(target, b'candidate')
            self.assertEqual(target.read_bytes(), b'candidate')
            self.assertEqual(other.read_bytes(), b'preserve')
            self.assertTrue(staging.is_symlink())
            self.assertEqual(set(root.iterdir()), {target, other, staging})

    def test_precommit_fsync_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as work:
            target = pathlib.Path(work)/'current.zone'
            target.write_bytes(b'old')
            with mock.patch.object(CORE.os, 'fsync', side_effect=OSError('simulated fsync failure')):
                with self.assertRaises(OSError):
                    CORE.atomic(target, b'candidate')
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(set(pathlib.Path(work).iterdir()), {target})

    def test_replace_failure_preserves_previous_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as work:
            target = pathlib.Path(work)/'current.zone'
            target.write_bytes(b'old')
            foreign = pathlib.Path(work)/'current.zone.new'
            foreign.write_bytes(b'foreign staging')

            def fail_replace(source, destination):
                source, destination = pathlib.Path(source), pathlib.Path(destination)
                self.assertEqual(source.parent, target.parent)
                self.assertNotEqual(source, foreign)
                self.assertEqual(source.read_bytes(), b'candidate')
                self.assertEqual(destination, target)
                self.assertEqual(target.read_bytes(), b'old')
                raise OSError('simulated replace failure')

            with mock.patch.object(CORE.os, 'replace', side_effect=fail_replace):
                with self.assertRaises(OSError):
                    CORE.atomic(target, b'candidate')
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(foreign.read_bytes(), b'foreign staging')
            self.assertEqual(set(pathlib.Path(work).iterdir()), {target, foreign})

    def test_destination_symlink_is_replaced_without_following_it(self):
        with tempfile.TemporaryDirectory() as work:
            root = pathlib.Path(work)
            other, target = root/'unrelated-public-data', root/'current.zone'
            other.write_bytes(b'preserve')
            target.symlink_to(other)
            CORE.atomic(target, b'candidate')
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), b'candidate')
            self.assertEqual(other.read_bytes(), b'preserve')

    def test_json_serialization_failure_leaves_existing_public_state(self):
        with tempfile.TemporaryDirectory() as work:
            target = pathlib.Path(work)/'publication.json'
            CORE.write_json(target, {'serial':SERIAL})
            before = target.read_bytes()
            with self.assertRaises(TypeError):
                CORE.write_json(target, {'unsupported':object()})
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(json.loads(before), {'serial':SERIAL})


if __name__ == '__main__':
    unittest.main()
