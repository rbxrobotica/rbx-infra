import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import headscale_inventory as h

class ObserverTests(unittest.TestCase):
    @unittest.skipIf(h.yaml is None, 'PyYAML is not installed; no installation requested')
    def test_yaml_rejects_alias_duplicate_depth_and_unsafe_tags(self):
        samples = (b'a: &x 1\nb: *x\n', b'a: 1\na: 2\n',
                   ('a: [' + '[' * 70 + '1' + ']' * 70 + ']\n').encode(),
                   b'a: !!python/object/apply:os.system ["false"]\n')
        for raw in samples:
            with self.subTest(raw=raw[:20]), self.assertRaises(h.Stop):
                h.parse_config(raw)

    def test_only_whitelisted_fields_and_no_secret_hostname_username_or_url(self):
        private = {'database': {'sqlite': {'path': '/var/lib/headscale/db.sqlite'}},
                   'server_url': 'https://secret-host.invalid', 'username': 'secret-user',
                   'noise': {'private_key_path': '/home/secret-user/secret-host.invalid.key'},
                   'oidc': {'client_secret': 'TOP-SECRET', 'issuer': 'https://secret-host.invalid'}}
        with mock.patch.object(h, 'metadata', return_value={'status': 'absent'}) as metadata:
            result = h.config_summary(private)
        encoded = json.dumps(result)
        for excluded in ('TOP-SECRET', 'secret-user', 'secret-host.invalid', 'https://', 'server_url', 'issuer'):
            self.assertNotIn(excluded, encoded)
        self.assertEqual(set(result['references']), set(h.REFS))
        self.assertTrue(result['flags']['oidc_inline_secret_present'])
        self.assertEqual(metadata.call_count, 2)

    def test_metadata_does_not_follow_symlink_ancestors_or_open_leaf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            leaf = root / 'db.sqlite'
            leaf.write_bytes(b'NEVER-READ')
            (root / 'link').symlink_to(root, target_is_directory=True)
            with mock.patch.object(h.os, 'read', side_effect=AssertionError('No content read')):
                self.assertEqual(h.metadata(str(leaf))['kind'], 'regular')
                self.assertEqual(h.metadata(str(root / 'link'))['kind'], 'symlink')
                self.assertEqual(h.metadata(str(root / 'link' / 'db.sqlite'))['status'],
                                 'non_directory_or_symlink_component')

    def test_tree_bounds_and_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deep = root / 'one' / 'two' / 'three' / 'four'
            deep.mkdir(parents=True)
            (deep / 'not-visited').write_bytes(b'never-open')
            (root / 'loop').symlink_to(root, target_is_directory=True)
            for i in range(260):
                (root / ('host-secret-' + str(i))).touch()
            with mock.patch.object(h.os, 'read', side_effect=AssertionError('No file content read')):
                result = h.tree_inventory((str(root),))
            self.assertLessEqual(result['entries_attempted'], 256)
            self.assertLessEqual(len(result['entries']), 256)
            self.assertTrue(result['truncated'])
            self.assertLessEqual(max(item['depth'] for item in result['entries']), 3)
            self.assertNotIn('host-secret', json.dumps(result))

    def test_systemctl_whitelist_and_dependency_refs_redacted(self):
        response = ('ActiveState=active\nSubState=running\nUnitFileState=enabled\n'
                    'ExecMainStatus=0\nResult=success\nFragmentPath=/lib/systemd/system/headscale.service\n'
                    'DropInPaths=/etc/systemd/system/secret-host.invalid.conf\n'
                    'EnvironmentFiles=/home/secret-user/secrets (ignore_errors=yes)\n'
                    'LastTriggerUSec=Sun 2026-11-08 12:00:00 UTC\nNextElapseUSecRealtime=\n')
        with mock.patch.object(h, 'query', return_value=response) as query, \
             mock.patch.object(h, 'metadata', return_value={'status': 'absent'}):
            result = h.unit_summary('headscale.service')
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['properties']['ActiveState'], 'active')
        self.assertEqual(result['properties']['ExecMainStatus'], 0)
        self.assertNotIn('secret-host', json.dumps(result))
        self.assertNotIn('secret-user', json.dumps(result))
        argv = query.call_args.args[0]
        self.assertEqual(argv[:3], ['/usr/bin/systemctl', 'show', '--no-pager'])
        self.assertEqual({item.removeprefix('--property=') for item in argv[3:-1]}, set(h.PROPERTIES))
        for name in ('ExecStart', 'Environment'):
            self.assertNotIn('--property=' + name, argv)

    def test_raw_subprocess_errors_and_unexpected_properties_never_escape(self):
        with mock.patch.object(h, 'query', side_effect=h.Stop('query_nonzero')):
            self.assertEqual(h.unit_summary('headscale.service'), {'status': 'query_nonzero'})
        with mock.patch.object(h, 'query', return_value='Environment=TOP-SECRET\n'):
            self.assertEqual(h.unit_summary('headscale.service'), {'status': 'systemctl_response_invalid'})

    def test_versions_query_package_metadata_without_running_headscale_or_gpg(self):
        with mock.patch.object(h, 'query', return_value='1.2.3-4\n') as query:
            result = h.versions()
        self.assertEqual(query.call_count, 3)
        for call in query.call_args_list:
            self.assertEqual(call.args[0][:3], ['/usr/bin/dpkg-query', '-W', '-f=${Version}\n'])
        self.assertEqual(result['headscale_package']['version'], '1.2.3-4')

    def test_deadline_is_not_swallowed_as_recoverable_config_error(self):
        self.assertFalse(issubclass(h.Deadline, Exception))
        with mock.patch.object(h, 'read_config', side_effect=h.Deadline), \
             mock.patch.object(h, 'tree_inventory') as tree:
            with self.assertRaises(h.Deadline):
                h.observe()
        tree.assert_not_called()

if __name__ == '__main__':
    unittest.main()
