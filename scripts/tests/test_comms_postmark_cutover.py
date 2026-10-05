import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('comms_postmark_cutover', Path(__file__).parents[1] / 'cutover-comms-postmark-webhooks.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class CutoverContract(unittest.TestCase):
    def setUp(self):
        self.credential = {'username': 'synthetic-user', 'password': 'synthetic-only'}
        self.server = {'ID': MOD.SERVER_ID, 'InboundHookUrl': 'https://' + MOD.OLD_HOST + '/api/webhooks/postmark/inbound'}
        self.hooks = [{'ID': key, 'Url': 'https://' + MOD.OLD_HOST + '/api/webhooks/postmark/' + path,
                       'MessageStream': 'outbound', 'HttpHeaders': [],
                       'Triggers': {trigger: {'Enabled': True, 'IncludeContent': False}, 'Open': {'Enabled': False}}}
                      for key, (path, trigger) in MOD.HOOKS.items()]

    def test_preserves_existing_ids_triggers_and_requires_provider_verification(self):
        before = copy.deepcopy(self.hooks)
        updates = MOD.plans(self.server, self.hooks, self.credential)
        self.assertEqual([key for key, _ in updates], list(MOD.HOOKS))
        for (_, body), original in zip(updates, before):
            self.assertEqual(body['Triggers'], original['Triggers'])
            self.assertTrue(body['Verify'])
            self.assertEqual(body['HttpAuth']['Password'], self.credential['password'])
            self.assertNotIn(self.credential['password'], body['Url'])
        self.assertEqual(self.hooks, before)

    def test_inventory_trigger_stream_origin_and_header_drift_stop(self):
        for mutation in (
            lambda s, h: s.update(ID=1),
            lambda s, h: h.pop(),
            lambda s, h: h[0].update(MessageStream='other'),
            lambda s, h: h[0].update(Url='https://unrelated.example.test/api/webhooks/postmark/bounce'),
            lambda s, h: h[0]['Triggers'].update(Click={'Enabled': True}),
            lambda s, h: h[0].update(HttpHeaders=[{'Name': 'Authorization', 'Value': 'never-print'}]),
            lambda s, h: h[0].update(HttpAuth={'Username': 'someone-else', 'Password': 'never-print'}),
        ):
            server, hooks = copy.deepcopy(self.server), copy.deepcopy(self.hooks)
            mutation(server, hooks)
            with self.assertRaises(MOD.CutoverError) as cm:
                MOD.plans(server, hooks, self.credential)
            self.assertNotIn('never-print', str(cm.exception))

    def test_exact_applied_configuration_can_be_reconciled_without_rotating(self):
        updates = MOD.plans(self.server, self.hooks, self.credential)
        self.server['InboundHookUrl'] = MOD.destination('inbound', self.credential)
        for hook, (_, body) in zip(self.hooks, updates):
            hook.update(body)
        self.assertEqual(MOD.plans(self.server, self.hooks, self.credential), updates)

    def test_inbound_basic_auth_is_encoded_only_for_intended_host(self):
        value = MOD.destination('inbound', self.credential)
        MOD.check_location(value, 'inbound', self.credential)
        with self.assertRaises(MOD.CutoverError):
            MOD.check_location(value.replace(MOD.NEW_HOST, MOD.OLD_HOST), 'inbound', self.credential)


if __name__ == '__main__':
    unittest.main()
