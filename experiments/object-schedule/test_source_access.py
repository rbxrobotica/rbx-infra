import unittest
from unittest.mock import patch
import source_access as s


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = s.policy('fixture-memory', 'arn:aws:iam::tenant:user/customer:worker')

    def test_exact_principal_and_no_wildcard_grant(self):
        for item in self.p['Statement']:
            self.assertEqual(item['Principal'], {'AWS': 'arn:aws:iam::tenant:user/customer:worker'})
        self.assertTrue(all('*' not in action for item in self.p['Statement']
                            for action in item.get('Action', [])))

    def test_explicit_write_denial(self):
        item = self.p['Statement'][2]
        self.assertEqual(item['Effect'], 'Deny')
        self.assertEqual(set(item['NotAction']), set(s.READ_BUCKET + s.READ_OBJECT))
        self.assertFalse(any('Put' in action or 'Delete' in action for action in item['NotAction']))

    def test_preserves_foreign_statements(self):
        old = {'Version': '2012-10-17', 'Statement': [{'Sid': 'Foreign', 'Effect': 'Deny'}]}
        result = s.merge(old, self.p)
        self.assertEqual(result['Statement'][0], old['Statement'][0])
        self.assertEqual(len(old['Statement']), 1)

    def test_rejects_duplicate_owned_changes(self):
        with self.assertRaises(ValueError): s.merge(self.p, self.p)

    def test_rejects_invalid_scope(self):
        for bucket, principal in [('*', 'arn:aws:iam::tenant:user/customer:worker'),
                                  ('fixture-memory', '*')]:
            with self.assertRaises(ValueError): s.policy(bucket, principal)

    def test_empty_mutations_send_valid_json_and_accept_no_content(self):
        requests=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b''
        class Opener:
            def open(self,request,timeout):requests.append(request);return Response()
        owner=object.__new__(s.Owner);owner.token='synthetic'
        with patch.object(s.urllib.request,'build_opener',return_value=Opener()):
            self.assertEqual(owner.api('users/fixture',method='DELETE'),{})
            owner.api('users/fixture')
        self.assertEqual(requests[0].get_method(),'DELETE')
        self.assertEqual(requests[0].data,b'{}')
        self.assertIsNone(requests[1].data)


if __name__ == '__main__': unittest.main()
