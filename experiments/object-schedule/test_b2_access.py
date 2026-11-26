import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).parent))
import b2_access as b2


class NativeAdmission(unittest.TestCase):
    def test_wrong_hosts_credentials_plaintext_or_ports_rejected(self):
        for url in ['http://api.backblazeb2.com/','https://api.backblazeb2.com.evil.invalid/',
                    'https://user:password@api005.backblazeb2.com/','https://api005.backblazeb2.com:8443/']:
            with self.assertRaises(ValueError):b2.destination(url)
    def test_expected_provider_host_is_admitted(self):
        self.assertEqual(b2.destination('https://api005.backblazeb2.com'),'https://api005.backblazeb2.com')
    def test_server_scope_normalizes_without_secrets(self):
        r={'apiInfo':{'storageApi':{'allowed':{'buckets':[{'id':'fixture'}],
             'capabilities':['writeFiles'],'namePrefix':'memory-auto/v1/'}}}}
        b2.access.admit('writer',b2.normalized(r),'fixture')
        self.assertNotIn('applicationKey',str(b2.normalized(r)))
    def test_unrestricted_bucket_scope_is_rejected(self):
        r={'apiInfo':{'storageApi':{'allowed':{'buckets':None,
             'capabilities':['writeFiles'],'namePrefix':'memory-auto/v1/'}}}}
        with self.assertRaises(ValueError):b2.access.admit('writer',b2.normalized(r),'fixture')
    def test_authorization_uses_only_the_provider_endpoint(self):
        calls=[]
        def transport(url,header,payload=None):
            calls.append((url,header,payload))
            return {'apiInfo':{'storageApi':{'apiUrl':'https://api005.backblazeb2.com'}}}
        b2.authorize('fixture-id','fixture-secret',transport)
        self.assertEqual(calls[0][0],b2.ROOT);self.assertIsNone(calls[0][2])
        self.assertTrue(calls[0][1].startswith('Basic '))


if __name__=='__main__':unittest.main()
