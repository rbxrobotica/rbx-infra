import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import json

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

    def test_read_only_reconciliation_after_partial_apply_never_writes(self):
        updates = MOD.plans(self.server, self.hooks, self.credential)
        self.hooks[0].update(updates[0][1])
        provider = mock.Mock()
        provider.request.side_effect = [self.server, {'Webhooks': self.hooks}]
        with mock.patch.object(MOD, 'Provider', return_value=provider), \
             mock.patch.object(MOD, 'secret', side_effect=['synthetic-token', json.dumps(self.credential)]), \
             mock.patch('builtins.print') as output:
            self.assertEqual(MOD.main(['--inspect-auth']), 0)
        self.assertEqual(provider.request.call_args_list, [mock.call('/server'), mock.call('/webhooks')])
        self.assertNotIn('synthetic-only', str(output.call_args_list))
        self.assertNotIn('synthetic-token', str(output.call_args_list))

    def test_inbound_basic_auth_is_encoded_only_for_intended_host(self):
        value = MOD.destination('inbound', self.credential)
        MOD.check_location(value, 'inbound', self.credential)
        with self.assertRaises(MOD.CutoverError):
            MOD.check_location(value.replace(MOD.NEW_HOST, MOD.OLD_HOST), 'inbound', self.credential)

    def apply_with_readback(self, mutation):
        updates = MOD.plans(self.server, self.hooks, self.credential)
        current_server = copy.deepcopy(self.server)
        current_server['InboundHookUrl'] = MOD.destination('inbound', self.credential)
        current_hooks = copy.deepcopy(self.hooks)
        for hook, (_, body) in zip(current_hooks, updates):
            hook.update(copy.deepcopy(body))
        mutation(current_hooks)
        provider = mock.Mock()
        provider.request.side_effect = [
            self.server, {'Webhooks': self.hooks}, {}, {}, {}, {},
            current_server, {'Webhooks': current_hooks},
        ]
        with mock.patch.object(MOD, 'Provider', return_value=provider), \
             mock.patch.object(MOD, 'secret', side_effect=['synthetic-token', json.dumps(self.credential)]), \
             mock.patch('builtins.print') as output:
            result = MOD.main(['--apply'])
        writes = [call for call in provider.request.call_args_list if len(call.args) == 2]
        self.assertEqual(writes, [mock.call('/webhooks/' + str(key), body) for key, body in updates] + [
            mock.call('/server', {'InboundHookUrl': MOD.destination('inbound', self.credential)})])
        self.assertNotIn('synthetic-only', str(output.call_args_list))
        self.assertNotIn('synthetic-token', str(output.call_args_list))
        self.assertNotIn('never-print', str(output.call_args_list))
        return result

    def test_apply_readback_accepts_only_null_or_empty_array_headers(self):
        for headers in (None, []):
            with self.subTest(headers=headers):
                self.assertEqual(self.apply_with_readback(
                    lambda hooks: [hook.update(HttpHeaders=headers) for hook in hooks]), 0)

    def test_apply_readback_rejects_nonempty_or_other_falsy_headers(self):
        for headers in ([{'Name': 'Authorization', 'Value': 'never-print'}], {}, '', False, 0):
            with self.subTest(headers_type=type(headers).__name__):
                self.assertEqual(self.apply_with_readback(
                    lambda hooks: hooks[0].update(HttpHeaders=headers)), 1)

    def test_apply_readback_still_requires_exact_url_auth_and_trigger_flags(self):
        for mutation in (
            lambda hooks: hooks[0].update(Url=hooks[0]['Url'].replace(MOD.NEW_HOST, MOD.OLD_HOST)),
            lambda hooks: hooks[0]['HttpAuth'].update(Password='never-print'),
            lambda hooks: hooks[0]['Triggers']['Bounce'].update(IncludeContent=True),
        ):
            with self.subTest(mutation=mutation):
                self.assertEqual(self.apply_with_readback(mutation), 1)


if __name__ == '__main__':
    unittest.main()
