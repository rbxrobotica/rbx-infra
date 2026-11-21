import copy
import importlib.util
from pathlib import Path
import unittest

s=importlib.util.spec_from_file_location('access',Path(__file__).with_name('access.py'))
access=importlib.util.module_from_spec(s);s.loader.exec_module(access)


class CapabilityAdmission(unittest.TestCase):
    def test_exact_writer_cannot_read_delete_or_administer(self):
        d=access.desired('writer','fixture-bucket')
        self.assertEqual(d['capabilities'],['writeFiles'])
        self.assertTrue(access.admit('writer',d,'fixture-bucket'))

    def test_exact_verifier_is_admitted(self):
        d=access.desired('verifier','fixture-bucket')
        self.assertTrue(access.admit('verifier',d,'fixture-bucket'))
        self.assertNotIn('writeFiles',d['capabilities'])

    def test_broader_capabilities_are_rejected(self):
        for cap in access.PLAN['destination']['forbidden_runtime_capabilities']+['readFiles']:
            d=access.desired('writer','fixture-bucket');d['capabilities']=d['capabilities']+[cap]
            with self.assertRaises(ValueError):access.admit('writer',d,'fixture-bucket')

    def test_another_bucket_or_all_buckets_are_rejected(self):
        for ids in [None,[],['other'],['fixture-bucket','other']]:
            d=access.desired('writer','fixture-bucket');d['bucketIds']=ids
            with self.assertRaises(ValueError):access.admit('writer',d,'fixture-bucket')

    def test_wider_or_wrong_prefix_is_rejected(self):
        for prefix in [None,'','memory-auto/','other/']:
            d=access.desired('writer','fixture-bucket');d['namePrefix']=prefix
            with self.assertRaises(ValueError):access.admit('writer',d,'fixture-bucket')

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):access.desired('admin','fixture-bucket')

    def test_expiration_is_bounded_to_ninety_days(self):
        self.assertEqual(access.desired('writer','fixture-bucket')['validDurationInSeconds'],90*86400)


if __name__=='__main__':unittest.main()
