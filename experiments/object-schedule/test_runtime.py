import base64
import json
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
import runtime as r


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.secret = b'a' * 32
        self.public = Ed25519PrivateKey.from_private_bytes(self.secret).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.body = dict(format='rbx-memory-workload-v1', dataset_id='C008',
            run_id='20261115T220000Z-' + 'a' * 32, started_epoch=1000, finished_epoch=1010,
            ciphertext_bytes=1, ciphertext_sha256=r.digest(b'x'), manifest_sha256='b' * 64,
            cipher_file_id='owned-fixture', cipher_name=r.PREFIX+'20261115T220000Z-'+'a'*32+'/archive.tar.gpg')

    def signed(self): return r.sign_receipt(self.body, self.secret)

    def test_valid_closed_receipt(self):
        self.assertEqual(r.verify_receipt(self.signed(), self.public, 1020), self.body)

    def test_tamper_is_not_accepted(self):
        changed = json.loads(self.signed()); changed['ciphertext_bytes'] = 2
        with self.assertRaises(InvalidSignature): r.verify_receipt(r.encoded(changed), self.public, 1020)

    def test_other_signer_not_accepted(self):
        with self.assertRaises(InvalidSignature):
            r.verify_receipt(r.sign_receipt(self.body, b'b'*32), self.public, 1020)

    def test_expired_and_future_receipt(self):
        for now in (0, 30000):
            with self.assertRaises(ValueError): r.verify_receipt(self.signed(), self.public, now)

    def test_cross_corpus_and_path_escape(self):
        for field, value in [('dataset_id', 'another-corpus'), ('cipher_name', '../other'),
                             ('run_id', '../../outside'), ('ciphertext_bytes', r.LIMIT+1)]:
            original = self.body[field]; self.body[field] = value
            with self.assertRaises(ValueError): r.verify_receipt(self.signed(), self.public, 1020)
            self.body[field] = original

    def test_retention_requires_authorized_compliance(self):
        valid = {'fileRetention': {'isClientAuthorizedToRead': True,
            'value': {'mode': 'compliance', 'retainUntilTimestamp': (1010+7*86400)*1000}}}
        r.retained(valid, self.body, 1020)
        for auth, mode, until in [(False,'compliance',999999999), (True,'governance',999999999),
                                   (True,'compliance',1000), (True,None,999999999)]:
            value = {'fileRetention': {'isClientAuthorizedToRead':auth,
                'value':{'mode':mode,'retainUntilTimestamp':until}}}
            with self.assertRaises(ValueError): r.retained(value,self.body,1020)

    def test_full_witness_readback_and_tamper(self):
        outer=self
        class Fake:
            rotation_due=False
            def latest(self): return {'fileName':r.PREFIX+outer.body['run_id']+'/receipt.json','fileId':'receipt'}
            def download_file(self, fid): return outer.signed() if fid=='receipt' else b'x'
            def api(self, name, payload):
                self.asserted = name == 'b2_get_file_info'
                return {'fileName':outer.body['cipher_name'],'contentLength':1,
                    'fileRetention':{'isClientAuthorizedToRead':True,
                        'value':{'mode':'compliance','retainUntilTimestamp':(1010+7*86400)*1000}}}
        client=Fake()
        self.assertTrue(r.witness(client,self.public,1020)['verified'])
        self.body['ciphertext_sha256']=r.digest(b'bad')
        with self.assertRaisesRegex(ValueError,'cipher_readback_mismatch'):
            r.witness(client,self.public,1020)

    def test_listing_is_bounded_and_selects_complete_receipt(self):
        client=object.__new__(r.B2);client.bucket_id='fixture'
        name=r.PREFIX+self.body['run_id']+'/receipt.json'
        client.api=lambda *args:{'files':[{'fileName':name,'fileId':'receipt'},
            {'fileName':r.PREFIX+'20990101T000000Z-'+'f'*32+'/archive.tar.gpg','fileId':'incomplete'}]}
        self.assertEqual(client.latest()['fileId'],'receipt')
        client.api=lambda *args:{'files':[],'nextFileName':'repeated'}
        with self.assertRaisesRegex(ValueError,'archive_pagination_repeated'): client.latest()

    def test_only_official_native_upload_pods_and_route(self):
        good='https://pod-050-1042-10.backblaze.com/b2api/v4/b2_upload_file?bucket=fixture'
        self.assertEqual(r.b2_access.upload_destination(good),good)
        for bad in [good.replace('https:','http:'),good.replace('.com/','.com.attacker.invalid/'),
                    good.replace('/b2api/v4/b2_upload_file','/another-route'),
                    good.replace('pod-050-1042-10','secure'),good.replace('https://','https://user@')]:
            with self.assertRaises(ValueError):r.b2_access.upload_destination(bad)
        with self.assertRaises(ValueError):r.b2_access.destination(good)


if __name__ == '__main__': unittest.main()
