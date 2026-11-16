import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
import urllib.request

SCRIPTS=Path(__file__).parents[1]
sys.path.insert(0,str(SCRIPTS))
def load(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/'flightdeck'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
owner=load('owner_copy');credentials=load('intake_credentials');restore=load('restore_check')


class OwnerCopy(unittest.TestCase):
    def test_exact_version_ciphertext_readback_is_retained_without_claiming_restore(self):
        data=b'synthetic ciphertext fixture';sha=hashlib.sha256(data).hexdigest()
        class Client:
            def latest(self,kind,days):
                self.kind=kind;return {'key':'flightdeck/backups/full/fixture.gpg','version_id':'version-1','sha256':sha,'age_seconds':10}
            def download(self,key,path,expected,version):
                self.downloaded=(key,expected,version);path.write_bytes(data)
                return {'verified':True,'version_id':version,'sha256':expected}
        client=Client()
        with tempfile.TemporaryDirectory() as directory:
            result=owner.preserve(client,Path(directory))
            self.assertEqual(client.downloaded[1:],(sha,'version-1'))
            self.assertEqual(client.kind,'full');self.assertFalse(result['restore_tested'])
            self.assertEqual(len(list(Path(directory).glob('*.gpg'))),1)
    def test_stale_or_unknown_is_an_error_and_never_a_zero_backup_count(self):
        for value in ({'status':'unknown'},{'age_seconds':93601}):
            class Client:
                def latest(self,*_):return value
                def download(self,*_):raise AssertionError('No stale download')
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(RuntimeError):owner.preserve(Client(),Path(directory))


class ScopedCredentials(unittest.TestCase):
    def test_patch_has_no_raw_capture_token_and_preserves_existing_fields(self):
        source={'metadata':{'resourceVersion':'1'},'data':{k:'Zml4dHVyZQ==' for k in
            ('DATABASE_URL','FLIGHTDECK_OPERATOR_EMAIL','OIDC_ALLOWED_EMAILS','SESSION_SECRET')}}
        original=source['data'].copy();seen=[]
        def command(argv,*,payload=None,**_):
            if argv[:2]==['pass','show']:
                if 'chat-source-thread-id' in argv[2]:return b'11111111-1111-4111-8111-111111111111'
                return (('a' if 'chat-capture' in argv[2] else 'b')*43).encode()
            if 'get' in argv:
                kind=argv[argv.index('get')+1]
                if kind=='namespace':return b'{"metadata":{"uid":"fixture-cluster"}}'
                if kind=='application':return b'{"spec":{"syncPolicy":{}}}'
                if kind=='secret':return json.dumps(source).encode()
            if 'patch' in argv:
                request=json.loads(payload);seen.append(request);source['data'].update(request['data']);return b'secret/name'
            raise AssertionError('Unexpected subprocess')
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config';config.write_text('synthetic');config.chmod(0o600)
            with patch.object(credentials,'command',side_effect=command),patch.object(sys,'argv',
                ['test','--kubeconfig',str(config),'--expected-cluster-uid','fixture-cluster','--apply']),contextlib.redirect_stdout(io.StringIO()) as output:
                credentials.main()
        self.assertEqual(set(seen[0]['data']),{'INTAKE_CHAT_TOKEN_SHA256','INTAKE_REVIEW_WORKER_KEY','INTAKE_CHAT_THREAD_ID'})
        self.assertTrue(all(source['data'][key]==value for key,value in original.items()))
        self.assertNotIn('a'*43,json.dumps(seen));self.assertNotIn('b'*43,output.getvalue())
        self.assertIn('"raw_capture_token_in_cluster": false',output.getvalue())


class ReviewTrigger(unittest.TestCase):
    def test_actual_job_request_is_json_and_does_not_enter_form_csrf_handling(self):
        manifest=yaml.safe_load((SCRIPTS.parent/'apps/prod/rbx-flightdeck/intake-review-cronjob.yml').read_text())
        code=manifest['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['args'][0]
        seen=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,*_):return json.dumps({'week':'2026-W47','cutoffAt':'2026-11-22T21:00:00Z',
                'prepared':1,'state':'AWAITING_HUMAN_REVIEW'}).encode()
        class Opener:
            def open(self,request,**_):seen.append(request);return Response()
        with patch.dict('os.environ',{'REVIEW_KEY':'a'*43}), \
             patch.object(urllib.request,'build_opener',return_value=Opener()),contextlib.redirect_stdout(io.StringIO()) as output:
            exec(compile(code,'synthetic-job-trigger','exec'),{})
        self.assertEqual(seen[0].get_header('Content-type'),'application/json')
        self.assertEqual(seen[0].get_method(),'POST');self.assertEqual(seen[0].data,b'{}')
        self.assertFalse(seen[0].has_header('Cookie'));self.assertFalse(seen[0].has_header('Origin'))
        self.assertNotIn('a'*43,output.getvalue())


class RestoreBoundary(unittest.TestCase):
    def test_pgdata_override_or_symlink_cannot_turn_unused_tmpfs_into_disk_restore(self):
        canonical='/var/lib/postgresql/data'
        restore.validate_data_directory(canonical+'\n',canonical+'\n')
        for configured,resolved in (('/other/path',canonical),(canonical,'/disk-backed/path'),
                                    ('/var/lib/postgresql/data/../other',canonical)):
            with self.assertRaises(RuntimeError):restore.validate_data_directory(configured,resolved)

    def test_host_network_mounts_privilege_and_non_recovery_targets_are_rejected(self):
        target={'Name':'flightdeck-recovery-proof-test','HostConfig':{'NetworkMode':'none',
            'Tmpfs':{'/var/lib/postgresql/data':'rw,size=512m'}},'Mounts':[]}
        restore.validate_target(target)
        for config in ({'NetworkMode':'host'},{'Privileged':True},{'PidMode':'host'}):
            with self.assertRaises(RuntimeError):restore.validate_target({**target,'HostConfig':{**target['HostConfig'],**config}})
        with self.assertRaises(RuntimeError):restore.validate_target({**target,'Name':'production-database'})
        with self.assertRaises(RuntimeError):restore.validate_target({**target,'Mounts':[{'Type':'bind','Destination':'/owner'}]})


if __name__=='__main__':unittest.main()
