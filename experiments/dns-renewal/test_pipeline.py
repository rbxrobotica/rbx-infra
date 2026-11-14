"""Offline pipeline tests: disposable in-memory keys and public temporary state."""

import base64
import hashlib
import importlib.util
import json
import pathlib
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import dns.dnssec
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.rdatatype
import dns.rrset
import dns.zone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import core
import probe
import renewer


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATEWAY = load_module('renewal_gateway_under_test', ROOT/'origin-gateway.py')
BUILDER = load_module('pipeline_public_fixture_builder',
                      ROOT.parent/'dns-origin/build-fixtures.py')
NOW = 1794620000
SERIAL = 2026111302
VIRTUAL_CREDENTIALS = pathlib.Path('/disposable-in-memory-test-credentials')


def raw_zone(zone):
    return zone.to_text(relativize=False, sorted=True).encode('ascii')


class PipelineFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = ed25519.Ed25519PrivateKey.generate()
        cls.dnskey = dns.dnssec.make_dnskey(cls.key.public_key(), 15, flags=257)
        cls.key_bytes = cls.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        cls.baseline = cls.signed()

    @classmethod
    def signed(cls, serial=SERIAL, expiration=NOW+core.LIFETIME, key=None,
               soa_changes=None, soa_ttl=30):
        key = cls.key if key is None else key
        dnskey = dns.dnssec.make_dnskey(key.public_key(), 15, flags=257)
        zone = dns.zone.from_text(BUILDER.unsigned(serial, 'pipeline-constant'),
                                  origin=core.ORIGIN, relativize=False)
        soa = zone.get_rdataset(core.ORIGIN, dns.rdatatype.SOA)
        if soa_changes:
            record = next(iter(soa)).replace(**soa_changes)
            soa.clear()
            soa.add(record, soa_ttl)
        soa.ttl = soa_ttl
        dns.dnssec.sign_zone(zone, keys=[(key, dnskey)],
                             inception=NOW-60, expiration=expiration)
        return zone

    def response(self, zone, kind='SOA', owner=core.ORIGIN):
        name = dns.name.from_text(owner)
        question = dns.message.make_query(owner, kind, want_dnssec=True)
        response = dns.message.make_response(question)
        response.flags |= dns.flags.AA
        data = zone.get_rdataset(name, dns.rdatatype.from_text(kind))
        signatures = zone.get_rdataset(name, dns.rdatatype.RRSIG,
                                       covers=dns.rdatatype.from_text(kind))
        response.answer.extend([
            dns.rrset.from_rdata_list(name, data.ttl, list(data)),
            dns.rrset.from_rdata_list(name, signatures.ttl, list(signatures)),
        ])
        return response

    def public_release(self, directory):
        directory.mkdir()
        raw = raw_zone(self.baseline)
        (directory/'baseline.zone').write_bytes(raw)
        ds = dns.dnssec.make_ds(self.baseline.origin, self.dnskey, 'SHA256').to_text()
        (directory/'policy.json').write_text(json.dumps({
            'origin': core.ORIGIN, 'synthetic_only': True,
            'baseline_sha256': hashlib.sha256(raw).hexdigest(), 'ds_sha256': ds,
        }))

    def inspect_receipt(self, raw):
        zone = core.parse(raw)
        expires = min(r.expiration for node in zone.nodes.values()
                      for data in node.rdatasets if data.rdtype == dns.rdatatype.RRSIG
                      for r in data)
        return {'zone_base64': base64.b64encode(raw).decode('ascii'),
                'sha256': hashlib.sha256(raw).hexdigest(),
                'valid_until_epoch': expires}


class ProbeRolloverControls(PipelineFixture):
    def test_authenticated_newer_soa_is_accepted(self):
        response = self.response(self.signed(serial=SERIAL+1))
        result = probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                                SERIAL, 86400, now=NOW)
        self.assertEqual(result['serial'], SERIAL+1)
        self.assertFalse(result['warning'])

    def test_signed_changes_to_other_soa_fields_are_rejected(self):
        changes = [
            {'refresh':31}, {'retry':11}, {'expire':121}, {'minimum':31},
            {'mname':dns.name.from_text('different.'+core.ORIGIN)},
            {'rname':dns.name.from_text('different-mail.'+core.ORIGIN)},
        ]
        for change in changes:
            with self.subTest(field=next(iter(change))):
                response = self.response(self.signed(serial=SERIAL+1,
                                                     soa_changes=change))
                with self.assertRaisesRegex(ValueError, 'data_changed'):
                    probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                                   SERIAL, 86400, now=NOW)

    def test_authentic_changed_soa_ttl_is_rejected(self):
        response = self.response(self.signed(serial=SERIAL+1, soa_ttl=31))
        with self.assertRaisesRegex(ValueError, 'shape_or_ttl'):
            probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                           SERIAL, 86400, now=NOW)

    def test_unpinned_key_cannot_authenticate_newer_soa(self):
        key = ed25519.Ed25519PrivateKey.generate()
        response = self.response(self.signed(serial=SERIAL+1, key=key))
        with self.assertRaises(dns.exception.DNSException):
            probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                           SERIAL, 86400, now=NOW)

    def test_changed_dnskey_response_is_rejected(self):
        key = ed25519.Ed25519PrivateKey.generate()
        response = self.response(self.signed(key=key), kind='DNSKEY')
        with self.assertRaisesRegex(ValueError, 'data_changed'):
            probe.evaluate(response, core.ORIGIN, 'DNSKEY', self.baseline,
                           SERIAL, 86400, now=NOW)

    def test_authenticated_rollback_and_half_range_are_rejected(self):
        for serial in [SERIAL-1, (SERIAL+0x80000000) & 0xffffffff]:
            with self.subTest(serial=serial):
                response = self.response(self.signed(serial=serial))
                with self.assertRaisesRegex(ValueError, 'serial_rollback_or_ambiguous'):
                    probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                                   SERIAL, 86400, now=NOW)

    def test_current_floor_rejects_an_older_still_valid_publication(self):
        response = self.response(self.baseline)
        with self.assertRaisesRegex(ValueError, 'rollback_or_ambiguous'):
            probe.evaluate(response, core.ORIGIN, 'SOA', self.baseline,
                           SERIAL+1, 86400, now=NOW)

    def test_run_checks_all_transports_and_requires_exact_convergence(self):
        zone = self.signed(serial=SERIAL+1)

        def answer(query, *args, **kwargs):
            return self.response(zone, dns.rdatatype.to_text(query.question[0].rdtype),
                                 query.question[0].name.to_text())

        with mock.patch.object(probe.dns.query, 'tcp', side_effect=answer) as tcp, \
                mock.patch.object(probe.dns.query, 'udp', side_effect=answer) as udp, \
                mock.patch.object(probe.time, 'time', return_value=NOW):
            healthy = probe.run(self.baseline, SERIAL, expected_serial=SERIAL+1)
            stalled = probe.run(self.baseline, SERIAL, expected_serial=SERIAL+2)
        self.assertEqual(healthy['exit_code'], 0)
        self.assertEqual(healthy['checks'], 24)
        self.assertEqual(healthy['serials'], [SERIAL+1])
        self.assertEqual(stalled['exit_code'], 2)
        self.assertEqual(tcp.call_count, 24)
        self.assertEqual(udp.call_count, 24)


class RenewerTransactionControls(PipelineFixture):
    def setup_state(self, root, due=False):
        release, state = root/'release', root/'state'
        self.public_release(release)
        state.mkdir()
        current = raw_zone(self.signed(expiration=NOW+2*86400) if due else self.baseline)
        (state/'current.zone').write_bytes(current)
        (state/'current.json').write_text(json.dumps({
            'serial':SERIAL, 'last_success_epoch':NOW-3600,
        }))
        return release, state, current

    def signer_reader(self):
        original = pathlib.Path.read_bytes
        reads = []

        def read(path):
            if path == VIRTUAL_CREDENTIALS/'signer':
                reads.append(path)
                return self.key_bytes
            return original(path)
        return read, reads

    def test_pending_bytes_replayed_after_failed_publish_or_lost_ack(self):
        for origin_committed in [False, True]:
            with self.subTest(origin_committed=origin_committed), \
                    tempfile.TemporaryDirectory() as work:
                release, state, old = self.setup_state(pathlib.Path(work), due=True)
                previous_receipt = (state/'current.json').read_bytes()
                origin = {'raw':old, 'payloads':[]}

                def remote(_release, _credentials, command, payload=None):
                    if command == 'inspect':
                        return self.inspect_receipt(origin['raw'])
                    self.assertEqual(command, 'publish')
                    origin['payloads'].append(payload)
                    if len(origin['payloads']) == 1:
                        if origin_committed:
                            origin['raw'] = payload
                        raise TimeoutError('simulated upload timeout or lost acknowledgement')
                    origin['raw'] = payload
                    return {'sha256':hashlib.sha256(payload).hexdigest(),
                            'origin_reload_verified':True}

                reader, key_reads = self.signer_reader()
                with mock.patch.object(renewer, 'remote', side_effect=remote), \
                        mock.patch.object(renewer.subprocess, 'run', return_value=
                                          subprocess.CompletedProcess([], 0, stdout='yes\n')), \
                        mock.patch.object(renewer.time, 'time', return_value=NOW), \
                        mock.patch.object(pathlib.Path, 'read_bytes', new=reader), \
                        mock.patch.object(renewer.probe, 'run', return_value={'exit_code':0, 'checks':24}) as health:
                    with self.assertRaises(TimeoutError):
                        renewer.execute(release, state, VIRTUAL_CREDENTIALS)
                    pending = (state/'pending.zone').read_bytes()
                    self.assertEqual((state/'current.zone').read_bytes(), old)
                    self.assertEqual((state/'current.json').read_bytes(), previous_receipt)
                    self.assertEqual(health.call_count, 0)
                    completed = renewer.execute(release, state, VIRTUAL_CREDENTIALS)
                self.assertEqual(origin['payloads'], [pending, pending])
                self.assertEqual(len(key_reads), 1)
                self.assertEqual(completed['mode'], 'resume_pending_publication')
                self.assertEqual(completed['serial'], SERIAL+1)
                self.assertTrue(completed['publication_fully_verified'])
                self.assertFalse((state/'pending.zone').exists())
                self.assertEqual((state/'current.zone').read_bytes(), pending)

    def test_convergence_failure_preserves_pending_and_previous_current(self):
        with tempfile.TemporaryDirectory() as work:
            release, state, old = self.setup_state(pathlib.Path(work), due=True)
            previous_receipt = (state/'current.json').read_bytes()
            payloads = []

            def remote(_release, _credentials, command, payload=None):
                if command == 'inspect':
                    return self.inspect_receipt(old)
                payloads.append(payload)
                return {'sha256':hashlib.sha256(payload).hexdigest(),
                        'origin_reload_verified':True}

            reader, _ = self.signer_reader()
            with mock.patch.object(renewer, 'remote', side_effect=remote), \
                    mock.patch.object(renewer.subprocess, 'run', return_value=
                                      subprocess.CompletedProcess([], 0, stdout='yes\n')), \
                    mock.patch.object(renewer.time, 'time', return_value=NOW), \
                    mock.patch.object(renewer.time, 'monotonic', side_effect=[0,181]), \
                    mock.patch.object(pathlib.Path, 'read_bytes', new=reader), \
                    mock.patch.object(renewer.probe, 'run', return_value={'exit_code':2, 'checks':24}):
                with self.assertRaisesRegex(ValueError, 'managed_publication_unconfirmed'):
                    renewer.execute(release, state, VIRTUAL_CREDENTIALS)
            self.assertEqual((state/'pending.zone').read_bytes(), payloads[0])
            self.assertEqual((state/'current.zone').read_bytes(), old)
            self.assertEqual((state/'current.json').read_bytes(), previous_receipt)

    def test_noop_requires_managed_health_without_reading_signer(self):
        for health_code in [0,2]:
            with self.subTest(health_code=health_code), tempfile.TemporaryDirectory() as work:
                release, state, old = self.setup_state(pathlib.Path(work))
                previous_receipt = (state/'current.json').read_bytes()
                reader, key_reads = self.signer_reader()
                with mock.patch.object(renewer, 'remote', return_value=self.inspect_receipt(old)) as remote, \
                        mock.patch.object(renewer.subprocess, 'run', return_value=
                                          subprocess.CompletedProcess([], 0, stdout='yes\n')), \
                        mock.patch.object(renewer.time, 'time', return_value=NOW), \
                        mock.patch.object(renewer.time, 'monotonic', side_effect=[0,181]), \
                        mock.patch.object(pathlib.Path, 'read_bytes', new=reader), \
                        mock.patch.object(renewer.probe, 'run', return_value={'exit_code':health_code, 'checks':24}) as health:
                    if health_code:
                        with self.assertRaisesRegex(ValueError, 'managed_publication_unconfirmed'):
                            renewer.execute(release, state, VIRTUAL_CREDENTIALS)
                        self.assertEqual((state/'current.json').read_bytes(), previous_receipt)
                    else:
                        result = renewer.execute(release, state, VIRTUAL_CREDENTIALS)
                        self.assertEqual(result['mode'], 'verified_noop')
                        self.assertTrue(result['publication_fully_verified'])
                self.assertEqual(len(key_reads), 0)
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(remote.call_args.args[2], 'inspect')
                self.assertEqual(health.call_args.kwargs['expected_serial'], SERIAL)
                self.assertFalse((state/'pending.zone').exists())
                self.assertEqual((state/'current.zone').read_bytes(), old)

    def test_clock_rollback_or_unsynchronized_clock_blocks_before_remote(self):
        for synchronized, last, error in [
            ('no\n', NOW-3600, 'clock_not_synchronized'),
            ('yes\n', NOW+121, 'clock_moved_backwards'),
        ]:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as work:
                release, state, old = self.setup_state(pathlib.Path(work))
                (state/'current.json').write_text(json.dumps({'serial':SERIAL,
                                                            'last_success_epoch':last}))
                before = (state/'current.json').read_bytes()
                with mock.patch.object(renewer.subprocess, 'run', return_value=
                                       subprocess.CompletedProcess([], 0, stdout=synchronized)) as clock, \
                        mock.patch.object(renewer.time, 'time', return_value=NOW), \
                        mock.patch.object(renewer, 'remote') as remote:
                    with self.assertRaisesRegex(ValueError, error):
                        renewer.execute(release, state, VIRTUAL_CREDENTIALS)
                self.assertEqual(clock.call_args.args[0][0], '/usr/bin/timedatectl')
                remote.assert_not_called()
                self.assertEqual((state/'current.zone').read_bytes(), old)
                self.assertEqual((state/'current.json').read_bytes(), before)


class GatewayPublicationControls(PipelineFixture):
    def pid_reader(self):
        original = pathlib.Path.read_text

        def read(path, *args, **kwargs):
            if path == pathlib.Path('/run/rbx-dns-renew-origin/nsd.pid'):
                return '12345\n'
            return original(path, *args, **kwargs)
        return read

    def test_wrong_original_commands_rejected_before_state_access(self):
        for command in ['', 'sh', 'inspect; id', 'publish ../other', 'inspect\npublish']:
            with self.subTest(command=command), \
                    mock.patch.dict(GATEWAY.os.environ, {'SSH_ORIGINAL_COMMAND':command}), \
                    mock.patch.object(GATEWAY.core, 'policy') as policy:
                with self.assertRaisesRegex(ValueError, 'command_not_allowed'):
                    GATEWAY.main()
                policy.assert_not_called()

    def test_different_bytes_with_equal_serial_are_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work)
            old = raw_zone(self.baseline)
            (state/'origin.zone').write_bytes(old)
            with mock.patch.object(GATEWAY, 'STATE', state), \
                    mock.patch.object(GATEWAY.time, 'time', return_value=NOW), \
                    mock.patch.object(GATEWAY.os, 'kill') as kill:
                with self.assertRaisesRegex(ValueError, 'serial_must_advance'):
                    GATEWAY.publish(old+b'\n', self.baseline)
            kill.assert_not_called()
            self.assertEqual((state/'origin.zone').read_bytes(), old)
            self.assertEqual(set(state.iterdir()), {state/'origin.zone'})

    def test_older_publication_is_rejected_before_signal_or_selection(self):
        with tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work)
            old = raw_zone(self.signed(serial=SERIAL+1))
            (state/'origin.zone').write_bytes(old)
            with mock.patch.object(GATEWAY, 'STATE', state), \
                    mock.patch.object(GATEWAY.time, 'time', return_value=NOW), \
                    mock.patch.object(GATEWAY.os, 'kill') as kill:
                with self.assertRaisesRegex(ValueError, 'rollback_or_ambiguous'):
                    GATEWAY.publish(raw_zone(self.baseline), self.baseline)
            kill.assert_not_called()
            self.assertEqual((state/'origin.zone').read_bytes(), old)

    def test_exact_retry_resignals_without_creating_another_release(self):
        with tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work)
            raw = raw_zone(self.baseline)
            (state/'origin.zone').write_bytes(raw)
            with mock.patch.object(GATEWAY, 'STATE', state), \
                    mock.patch.object(GATEWAY.time, 'time', return_value=NOW), \
                    mock.patch.object(pathlib.Path, 'read_text', new=self.pid_reader()), \
                    mock.patch.object(GATEWAY.os, 'kill') as kill, \
                    mock.patch.object(GATEWAY, 'verify_origin') as verify:
                result = GATEWAY.publish(raw, self.baseline)
            kill.assert_called_once_with(12345, signal.SIGHUP)
            verify.assert_called_once_with(SERIAL, self.baseline)
            self.assertTrue(result['idempotent_retry'])
            self.assertTrue(result['origin_reload_verified'])
            self.assertEqual((state/'origin.zone').read_bytes(), raw)
            self.assertFalse((state/'releases').exists())

    def test_new_publication_retry_after_unconfirmed_reload_is_idempotent(self):
        with tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work)
            (state/'origin.zone').write_bytes(raw_zone(self.baseline))
            candidate, _ = core.renew(self.key_bytes, self.baseline, SERIAL, now=NOW)
            with mock.patch.object(GATEWAY, 'STATE', state), \
                    mock.patch.object(GATEWAY.time, 'time', return_value=NOW), \
                    mock.patch.object(pathlib.Path, 'read_text', new=self.pid_reader()), \
                    mock.patch.object(GATEWAY.os, 'kill') as kill, \
                    mock.patch.object(GATEWAY, 'verify_origin', side_effect=[
                        ValueError('simulated unconfirmed reload'), None]) as verify:
                with self.assertRaisesRegex(ValueError, 'unconfirmed reload'):
                    GATEWAY.publish(candidate, self.baseline)
                self.assertEqual((state/'origin.zone').read_bytes(), candidate)
                self.assertFalse((state/'last-accepted.json').exists())
                retried = GATEWAY.publish(candidate, self.baseline)
            self.assertEqual(kill.call_count, 2)
            self.assertEqual(verify.call_count, 2)
            self.assertTrue(retried['idempotent_retry'])
            self.assertEqual(retried['serial'], SERIAL+1)
            archive = state/'releases'/(hashlib.sha256(candidate).hexdigest()+'.zone')
            self.assertEqual(archive.read_bytes(), candidate)
            self.assertEqual(set((state/'releases').iterdir()), {archive})
            self.assertEqual(json.loads((state/'last-accepted.json').read_text())
                             ['sha256'], hashlib.sha256(candidate).hexdigest())


if __name__ == '__main__':
    unittest.main()
