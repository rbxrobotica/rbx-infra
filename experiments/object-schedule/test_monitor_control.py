import copy
import importlib.util
from pathlib import Path
import unittest

s=importlib.util.spec_from_file_location('mc',Path(__file__).with_name('monitor_control.py'))
mc=importlib.util.module_from_spec(s);s.loader.exec_module(mc)


class Transport:
    def __init__(self):
        self.checks=[{'uuid':'other-fixture','name':'Unrelated check','slug':'other',
                      'timeout':900,'grace':900,'channels':'existing-email','schedule':None}]
        self.writes=[]
    def __call__(self,key,url=None,payload=None):
        if payload is None:return {'checks':copy.deepcopy(self.checks)}
        self.writes.append(payload)
        check={k:v for k,v in payload.items() if k not in ('unique',)}
        check.update(uuid='owned-fixture',update_url=mc.hc.API+'owned-fixture',
                     ping_url='https://hc-ping.com/11111111-1111-4111-8111-111111111111',channels='existing-email')
        self.checks=[x for x in self.checks if x['name']!=check['name']]+[check]
        return copy.deepcopy(check)


class CheckManagement(unittest.TestCase):
    def test_plan_never_mutates(self):
        t=Transport();r=mc.reconcile('capture',False,t,'fixture',lambda *a:None)
        self.assertTrue(r['change_needed']);self.assertEqual(t.writes,[])
    def test_apply_creates_only_owned_check_and_preserves_existing(self):
        t=Transport();prior=copy.deepcopy(t.checks[0]);stored=[]
        r=mc.reconcile('capture',True,t,'fixture',lambda *a:stored.append(a))
        self.assertEqual(t.checks[0],prior);self.assertEqual(len(t.writes),1)
        self.assertTrue(r['notification_channel_attached']);self.assertEqual(len(stored),1)
        self.assertEqual(stored[0][0],'rbx/healthchecks/memory-capture')
    def test_repeated_apply_is_idempotent(self):
        t=Transport()
        mc.reconcile('witness',True,t,'fixture',lambda *a:None)
        mc.reconcile('witness',True,t,'fixture',lambda *a:None)
        self.assertEqual(len(t.writes),1)
    def test_duplicate_name_refused(self):
        t=Transport();name,slug,*_=mc.CHECKS['capture']
        c={'name':name,'slug':slug};t.checks.extend([c,c])
        with self.assertRaises(ValueError):mc.reconcile('capture',True,t,'fixture')
        self.assertEqual(t.writes,[])
    def test_foreign_slug_is_not_taken_over(self):
        t=Transport();t.checks.append({'name':mc.CHECKS['capture'][0],'slug':'foreign'})
        with self.assertRaises(ValueError):mc.reconcile('capture',True,t,'fixture')
        self.assertEqual(t.writes,[])
    def test_unknown_role_refused(self):
        with self.assertRaises(ValueError):mc.reconcile('other',True,Transport(),'fixture')
    def test_pings_and_management_key_are_not_in_reports(self):
        r=mc.reconcile('witness',True,Transport(),'secret-fixture-api',lambda *a:None)
        self.assertNotIn('secret-fixture-api',str(r));self.assertNotIn('hc-ping.com',str(r))


if __name__=='__main__':unittest.main()
