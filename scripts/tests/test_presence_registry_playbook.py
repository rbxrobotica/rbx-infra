"""Execute the actual scoped Ansible flow against synthetic local commands only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / 'bootstrap/ansible/public-presence-registry.yml'
UID = '7c54362a-7c64-4d62-a6ec-a14d4843e8a5'
TOKEN = 'synthetic_registry_value_not_real'

KUBECTL = '''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]; base=pathlib.Path(os.environ['PRESENCE_FIXTURE_DIR'])
scenario=os.environ['PRESENCE_FIXTURE_CASE']
with (base/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if 'apply' in args:
    payload=json.load(sys.stdin)
    assert payload['kind']=='Secret' and payload['metadata']=={'name':'ghcr-pull-secret','namespace':'rbx-public-presence'}
    assert payload['type']=='kubernetes.io/dockerconfigjson'
    (base/'applied').write_text(json.dumps(payload))
    print('secret/ghcr-pull-secret configured'); raise SystemExit
if 'kube-system' in args: print('different-cluster' if scenario=='cluster' else '%s')
elif 'application' in args:
    app={'kind':'Application','metadata':{'name':'rbx-public-presence','namespace':'argocd'},'spec':{'syncPolicy':{},'destination':{'namespace':'rbx-public-presence'}},'status':{}}
    if scenario=='wrong-app': app['metadata']['name']='another-app'
    if scenario=='autosync': app['spec']['syncPolicy']['automated']={'selfHeal':True}
    if scenario=='operation': app['status']['operationState']={'phase':'Running'}
    print(json.dumps(app))
elif 'job' in args:
    if scenario=='api-error': raise SystemExit(1)
    if scenario=='malformed-job': print('{}'); raise SystemExit
    print(json.dumps({'kind':'Job','metadata':{'name':'rbx-public-presence-db-migrate','namespace':'rbx-public-presence'},'spec':{'suspend':scenario!='unsuspended'},'status':{'active':1 if scenario=='active' else 0}}))
elif 'pods' in args:
    print(json.dumps({'items':[{'status':{'phase':'Pending'}}] if scenario=='pod' else []}))
else: raise SystemExit(9)
''' % UID

PASS = '''#!/usr/bin/env python3
import os,pathlib,sys
assert sys.argv[1:]==['show','rbx/cluster/ghcr-token']
pathlib.Path(os.environ['PRESENCE_FIXTURE_DIR'],'pass-read').write_text('yes')
print('%s')
''' % TOKEN


@unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible required for the executable scope test')
class RegistryPlaybookTests(unittest.TestCase):
    def run_case(self, scenario, *, check=False):
        with tempfile.TemporaryDirectory(prefix='presence-ansible-fixture-') as directory:
            base = Path(directory)
            for name, script in [('kubectl', KUBECTL), ('pass', PASS)]:
                path = base / name
                path.write_text(script)
                path.chmod(0o700)
            env = {**os.environ, 'PATH':str(base)+os.pathsep+os.environ['PATH'],
                   'PRESENCE_FIXTURE_DIR':str(base), 'PRESENCE_FIXTURE_CASE':scenario,
                   'ANSIBLE_NOCOLOR':'1', 'ANSIBLE_LOCAL_TEMP':str(base/'ansible')}
            argv = ['ansible-playbook','-i','localhost,',str(PLAYBOOK)]
            if check: argv += ['--check']
            result = subprocess.run(argv,env=env,text=True,capture_output=True,timeout=45,check=False)
            output = result.stdout + result.stderr
            self.assertNotIn(TOKEN, output)
            return result.returncode, (base/'pass-read').exists(), (base/'applied').exists(), output

    def test_real_playbook_guards_fail_before_credentials_or_apply(self):
        for scenario in ('cluster','wrong-app','autosync','operation','unsuspended','active','pod','api-error','malformed-job'):
            with self.subTest(scenario=scenario):
                code, read, applied, _ = self.run_case(scenario)
                self.assertNotEqual(code,0)
                self.assertFalse(read)
                self.assertFalse(applied)

    def test_check_mode_never_reads_pass_or_applies_even_when_ready(self):
        code, read, applied, output = self.run_case('ready',check=True)
        self.assertEqual(code,0,output)
        self.assertFalse(read)
        self.assertFalse(applied)

    def test_ready_execution_writes_only_the_namespaced_registry_secret(self):
        code, read, applied, output = self.run_case('ready')
        self.assertEqual(code,0,output)
        self.assertTrue(read)
        self.assertTrue(applied)


if __name__=='__main__':
    unittest.main()
