"""Offline guards around a real but strictly synthetic engine invocation."""
import json,tempfile,unittest
from pathlib import Path
from unittest import mock
import core,engine


def binding():
    run='20261119-aaaaaaaa'
    return {'run_id':run,'prefix':'qualification/v1/'+run+'/',
      'endpoint':'https://s3.us-east-005.backblazeb2.com',
      'buckets':{x:{'bucketName':'rbx-tofu-'+x+'-'+run,'bucketId':x+'-fixture-id'} for x in ('state','archive')}}


class EngineGuards(unittest.TestCase):
    def test_launcher_rejects_alternate_backend_destroy_state_mutation_and_environment_drift(self):
        with tempfile.TemporaryDirectory() as temp,mock.patch.object(core,'BASE',Path(temp)):
            working=engine.configure(binding(),Path(temp)/'owned','a')
            env=engine.environment_base(working,1,'synthetic-test-phrase')
            for args in (['init','-backend-config=/other'],['apply','-destroy'],
                         ['state','push','other.tfstate'],['state','rm','terraform_data.fixture']):
                with self.subTest(args=args),self.assertRaisesRegex(ValueError,'unreviewed_engine_arguments'):
                    engine.admit_invocation(working,env,args)
            args=['plan','-input=false','-no-color','-lock-timeout=2s']
            engine.admit_invocation(working,env,args)
            for change in ({'TF_DATA_DIR':'/other'},{'TF_CLI_CONFIG_FILE':'/other'},
                           {'TF_CLI_ARGS_apply':'-destroy'},{'AWS_CONFIG_FILE':'/other'}):
                with self.subTest(change=change),self.assertRaisesRegex(ValueError,'unreviewed_engine_environment'):
                    engine.admit_invocation(working,{**env,**change},args)

    def test_timeout_is_recorded_separately_from_expected_negative_classifications(self):
        process=mock.Mock(returncode=-2)
        process.communicate.side_effect=[engine.subprocess.TimeoutExpired('synthetic',1),
                                        (b'',b'encryption diagnostic after interruption')]
        summary,_,_=engine.finish(process,timeout=1)
        self.assertTrue(summary['timed_out'])
        process.send_signal.assert_called_once_with(2)

    def test_launcher_rejects_configuration_drift_and_extra_code_before_execution(self):
        with tempfile.TemporaryDirectory() as temp,mock.patch.object(core,'BASE',Path(temp)):
            working=engine.configure(binding(),Path(temp)/'owned','a')
            engine.admit_working(working)
            for name in ('extra.tf','extra.tofu','fixture.tofu.json','input.sym.hcl','secret.auto.tfvars','input.auto.tfvars.json','errored.tfstate'):
                extra=working/name;extra.write_text('{}')
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'unexpected_configuration'):engine.admit_working(working)
                extra.unlink()
            cli=working/'cli.tfrc';cli.write_text('disable_checkpoint = false\n')
            with self.assertRaisesRegex(ValueError,'fixture_content_drift'):engine.admit_working(working)
            cli.write_text('disable_checkpoint = true\n')
            config=working/'fixture.tf.json';config.write_text('{}')
            with self.assertRaisesRegex(ValueError,'fixture_content_drift'):engine.admit_working(working)

    def test_empty_userinfo_refused(self):
        for prefix in ('@',':@'):
            with self.assertRaises(ValueError):core.endpoint('https://'+prefix+'s3.us-east-005.backblazeb2.com')

    def test_config_has_only_builtin_fixture_and_nonsecret_endpoint_binding(self):
        with tempfile.TemporaryDirectory() as temp,mock.patch.object(core,'BASE',Path(temp)):
            root=Path(temp)/'run'
            work=engine.configure(binding(),root,'a')
            config=json.loads((work/'fixture.tf.json').read_text())
            self.assertEqual(set(config['resource']),{'terraform_data'})
            self.assertNotIn('provider',config)
            backend=config['terraform']['backend']['s3']
            self.assertTrue(backend['use_lockfile']);self.assertTrue(backend['encrypt'])
            self.assertTrue(backend['key'].startswith(binding()['prefix']+'active/'))
            self.assertNotIn('access_key',backend);self.assertNotIn('secret_key',backend)
            with self.assertRaises(ValueError):engine.configure(binding(),root,'a','http://remote.invalid')

    def test_process_env_excludes_ambient_cloud_and_cli_injection(self):
        b=binding();ids,prefix=core.scope(b,'runtime')
        key={'applicationKeyId':'LAB_ID','applicationKey':'SYNTHETIC_ONLY_SECRET',
             'capabilities':core.CAPS['runtime'],'bucketIds':ids,'namePrefix':prefix}
        def stored(name,lab):
            self.assertTrue(lab)
            return json.dumps(key if name.endswith('/runtime') else {'passphrase':'synthetic-only-passphrase-64'}).encode()
        with mock.patch.object(core,'stored',side_effect=stored),mock.patch.dict('os.environ',{
           'AWS_SESSION_TOKEN':'AMBIENT_PRIVATE','TF_CLI_ARGS_apply':'AMBIENT_PRIVATE',
           'TF_LOG':'TRACE','AWS_CA_BUNDLE':'AMBIENT_PRIVATE'}):
            env=engine.environment(b,Path('/fixture/a'),1)
        for key in ('AWS_SESSION_TOKEN','TF_CLI_ARGS_apply','TF_LOG','AWS_CA_BUNDLE'):self.assertNotIn(key,env)
        self.assertEqual(env['AWS_CONFIG_FILE'],'/dev/null')
        self.assertIn('enforced = true',env['TF_ENCRYPTION'])

    def test_binary_guard_rejects_unpinned_file_without_execution(self):
        with tempfile.NamedTemporaryFile() as file,mock.patch.object(engine.subprocess,'Popen') as execute:
            with self.assertRaises(ValueError):engine.verify_binary(Path(file.name))
            execute.assert_not_called()

    def test_engine_output_returns_only_classification_and_digest(self):
        process=mock.Mock(returncode=1);process.communicate.return_value=(b'SYNTHETIC_PRIVATE_SENTINEL',b'Error acquiring the state lock')
        summary,out,err=engine.finish(process)
        self.assertTrue(summary['lock_error']);self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(summary))
        self.assertFalse(summary['raw_output_retained'])


if __name__=='__main__':unittest.main()
