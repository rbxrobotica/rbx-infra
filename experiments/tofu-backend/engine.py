"""Pinned real engine over a fixed synthetic-only configuration."""
import json,os,re,shlex,subprocess,time
from pathlib import Path
import core

BINARY_SHA256='9ce9173bf161f6466ef57dab7edebe8f55089fdf81caa967415323c25d004990'


def verify_binary(binary):
    path=Path(binary)
    if path.resolve()!=path or not path.is_file() or core.digest(path.read_bytes())!=BINARY_SHA256:
        raise ValueError('unreviewed_engine_binary')
    return str(path)


def configure(binding,root,name,endpoint_override=None):
    core.admit(binding)
    if name not in ('a','b','offline','unavailable','denied'):raise ValueError('unexpected_engine_name')
    root=core.owned_dir(Path(root));working=core.owned_dir(root/name)
    for folder in ('data','gate'):core.owned_dir(working/folder)
    endpoint=endpoint_override or binding['endpoint']
    if endpoint!=binding['endpoint'] and not (name=='unavailable' and endpoint=='https://127.0.0.1:1'):
        raise ValueError('unreviewed_endpoint_override')
    backend={'bucket':binding['buckets']['state']['bucketName'],'key':binding['prefix']+'active/state.tfstate',
       'workspace_key_prefix':binding['prefix']+'active/workspaces',
       'region':binding['endpoint'].split('.')[1], 'endpoints':{'s3':endpoint},
       'use_path_style':True,'encrypt':True,'use_lockfile':True,'skip_credentials_validation':True,
       'skip_region_validation':True,'skip_requesting_account_id':True,'skip_metadata_api_check':True,
       'skip_s3_checksum':True,'retry_mode':'standard','max_retries':0}
    return configure_backend(binding['run_id'],root,name,{'s3':backend})


def configure_backend(run_id,root,name,backend):
    """Write only the builtin fixture; each provider adapter must admit its binding."""
    if name not in ('a','b','offline','unavailable','denied'):raise ValueError('unexpected_engine_name')
    working=core.owned_dir(core.owned_dir(Path(root))/name)
    for folder in ('data','gate'):core.owned_dir(working/folder)
    config={'terraform':{'required_version':'= '+core.PINS['version'],'backend':backend,
                 'encryption':{'state':{'enforced':True},'plan':{'enforced':True}}},
       'variable':{'generation':{'type':'number'}},
       'resource':{'terraform_data':{'fixture':{
          'input':{'run_id':run_id,'classification':'synthetic-only','generation':'${var.generation}',
                   'canary':'RBX_SYNTHETIC_TOFU_CANARY_V1'},
          'triggers_replace':['${var.generation}'],
          'provisioner':[{'local-exec':{'command':shlex.join(['/usr/bin/python3','-B',str(core.HERE/'hold.py')])}}]}}},
       'output':{'generation':{'value':'${terraform_data.fixture.output.generation}'}}}
    content=json.dumps(config,indent=2).encode();path=working/'fixture.tf.json'
    if path.exists():
        if path.read_bytes()!=content:raise ValueError('engine_fixture_drift')
    else:core.private_write(path,content)
    cli=working/'cli.tfrc'
    if cli.exists():
        if cli.is_symlink() or cli.read_bytes()!=b'disable_checkpoint = true\n':raise ValueError('unreviewed_cli_configuration')
    else:core.private_write(cli,b'disable_checkpoint = true\n')
    seal(working)
    return working


def seal(working):
    value={'configuration_sha256':core.digest((working/'fixture.tf.json').read_bytes()),
           'cli_sha256':core.digest((working/'cli.tfrc').read_bytes()),
           'provisioner_sha256':core.digest((core.HERE/'hold.py').read_bytes())}
    path=working/'fixture-guard.json'
    if path.exists():
        if json.loads(path.read_text())!=value:raise ValueError('existing_fixture_guard_drift')
    else:core.private_write(path,json.dumps(value).encode())


def admit_working(working):
    core.owned_dir(working)
    # OpenTofu also loads .tofu/.tofu.json and automatic variable inputs.
    # A strict directory allowlist prevents shadowing the hashed .tf.json.
    allowed={'fixture.tf.json','fixture-guard.json','cli.tfrc','data','gate'}
    if any(p.name not in allowed or p.is_symlink() for p in working.iterdir()):
        raise ValueError('unexpected_configuration_or_local_state')
    core.owned_dir(working/'data');core.owned_dir(working/'gate')
    value=json.loads((working/'fixture-guard.json').read_text())
    if (value['configuration_sha256']!=core.digest((working/'fixture.tf.json').read_bytes())
        or value.get('cli_sha256')!=core.digest((working/'cli.tfrc').read_bytes())
        or value['provisioner_sha256']!=core.digest((core.HERE/'hold.py').read_bytes())):
        raise ValueError('fixture_content_drift')


def environment(binding,working,generation,crypto='correct',hold=False,bad_credentials=False):
    core.admit(binding);key=json.loads(core.stored(core.PASS_PREFIX+'runtime',True))
    buckets,prefix=core.scope(binding,'runtime')
    if set(key['capabilities'])!=set(core.CAPS['runtime']) or key['bucketIds']!=buckets or key['namePrefix']!=prefix:
        raise ValueError('engine_runtime_scope_drift')
    phrase=json.loads(core.stored(core.PASS_PREFIX+'encryption',True))['passphrase']
    env=environment_base(working,generation,phrase,crypto,hold)
    env.update({'AWS_ACCESS_KEY_ID':key['applicationKeyId'],
      'AWS_SECRET_ACCESS_KEY':('invalid-synthetic-credential' if bad_credentials else key['applicationKey']),
      'AWS_EC2_METADATA_DISABLED':'true','AWS_CONFIG_FILE':'/dev/null','AWS_SHARED_CREDENTIALS_FILE':'/dev/null'})
    return env


def environment_base(working,generation,phrase,crypto='correct',hold=False):
    if crypto not in ('correct','missing','wrong'):raise ValueError('unexpected_encryption_case')
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(Path.home()),'CHECKPOINT_DISABLE':'1',
      'TF_IN_AUTOMATION':'1','TF_INPUT':'0','TF_DATA_DIR':str(working/'data'),
      'TF_CLI_CONFIG_FILE':str(working/'cli.tfrc'),'TF_VAR_generation':str(generation),
      'RBX_TOFU_GATE':str(working/'gate'),'RBX_TOFU_HOLD':'1' if hold else '0'}
    if crypto!='missing':
        phrase=phrase if crypto=='correct' else 'wrong-synthetic-encryption-passphrase-never-valid'
        env['TF_ENCRYPTION']='key_provider "pbkdf2" "lab" { passphrase = '+json.dumps(phrase)+' }\n'+'''method "aes_gcm" "lab" { keys = key_provider.pbkdf2.lab }
state {
 method = method.aes_gcm.lab
 enforced = true
}
plan {
 method = method.aes_gcm.lab
 enforced = true
}
'''
    return env


def start(binary,working,env,args):
    verify_binary(binary)
    admit_working(working)
    if args[0] not in ('init','apply','show','state','force-unlock','plan','version'):
        raise ValueError('engine_operation_not_allowed')
    if any(x in ('-lock=false','-migrate-state','-force-copy') for x in args):
        raise ValueError('unsafe_engine_flag')
    admit_invocation(working,env,args)
    return subprocess.Popen([str(binary),*args],cwd=working,env=env,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)


def admit_invocation(working,env,args):
    """Only the exact scenarios used by this experiment may reach the engine."""
    fixed={('init','-input=false','-no-color','-reconfigure'),('state','pull'),
           ('show','-json'),('version','-json')}
    for timeout in ('1s','2s'):
        fixed.add(('plan','-input=false','-no-color','-lock-timeout='+timeout))
        fixed.add(('apply','-input=false','-no-color','-auto-approve','-lock-timeout='+timeout))
    artifact=(len(args)==3 and args[:2]==['show','-json'])
    negative_unlock=(len(args)==3 and args[:2]==['force-unlock','-force'] and
        re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',args[2]))
    if tuple(args) not in fixed and not artifact and not negative_unlock:
        raise ValueError('unreviewed_engine_arguments')
    if artifact:
        path=Path(args[2])
        if (not path.is_absolute() or path.resolve()!=path or not path.is_relative_to(core.BASE)
            or path.suffix!='.enc' or not path.is_file() or path.stat().st_uid!=os.getuid()
            or path.stat().st_mode&0o077 or path.stat().st_size>1048576):
            raise ValueError('unreviewed_cipher_artifact')
    allowed={'PATH','LANG','HOME','CHECKPOINT_DISABLE','TF_IN_AUTOMATION','TF_INPUT',
      'TF_DATA_DIR','TF_CLI_CONFIG_FILE','TF_VAR_generation','TF_ENCRYPTION',
      'RBX_TOFU_GATE','RBX_TOFU_HOLD','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY',
      'AWS_SSE_CUSTOMER_KEY','AWS_EC2_METADATA_DISABLED','AWS_CONFIG_FILE',
      'AWS_SHARED_CREDENTIALS_FILE','AWS_MAX_ATTEMPTS'}
    expected={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(Path.home()),
      'CHECKPOINT_DISABLE':'1','TF_IN_AUTOMATION':'1','TF_INPUT':'0',
      'TF_DATA_DIR':str(working/'data'),'TF_CLI_CONFIG_FILE':str(working/'cli.tfrc'),
      'RBX_TOFU_GATE':str(working/'gate')}
    if (set(env)-allowed or any(env.get(k)!=v for k,v in expected.items())
        or env.get('RBX_TOFU_HOLD') not in ('0','1')
        or not re.fullmatch(r'[1-9][0-9]?',env.get('TF_VAR_generation',''))
        or any(env.get(k,'/dev/null')!='/dev/null' for k in ('AWS_CONFIG_FILE','AWS_SHARED_CREDENTIALS_FILE'))):
        raise ValueError('unreviewed_engine_environment')


def finish(process,timeout=90):
    started=time.monotonic();timed_out=False
    try:out,err=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True
        process.send_signal(2)
        try:out,err=process.communicate(timeout=8)
        except subprocess.TimeoutExpired:process.kill();out,err=process.communicate(timeout=5)
    combined=out+err
    if len(combined)>2*1024*1024:raise ValueError('engine_output_bound')
    lower=combined.lower()
    summary={'exit_code':process.returncode,'observed_wait_seconds':round(time.monotonic()-started,4),
      'timed_out':timed_out,
      'output_sha256':core.digest(combined),'output_bytes':len(combined),
      'lock_error':b'error acquiring the state lock' in lower,
      'backend_error':any(x in lower for x in (b'backend initialization required',b'failed to get existing workspaces',b'connection refused',b'invalidaccesskeyid',b'signaturedoesnotmatch',b'accessdenied',b'access denied',b'forbidden')),
      'encryption_error':any(x in lower for x in (b'encrypt',b'decrypt',b'key provider')),
      'artifact_error':any(x in lower for x in (b'failed to read',b'invalid',b'could not read',b'unable to read',b'error loading state')),
      'raw_output_retained':False}
    return summary,out,err


def run(binary,working,env,args,timeout=90):
    return finish(start(binary,working,env,args),timeout)
