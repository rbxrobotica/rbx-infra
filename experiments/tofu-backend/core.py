"""Synthetic-only fixture guards and custody. No operation runs on import."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import urllib.parse

HERE=Path(__file__).resolve().parent
BASE=Path.home()/'.local/state/rbx-tofu-qualification'
PASS_STORE=BASE/'custody'
PASS_PREFIX='rbx/tofu-backend/'
PINS=json.loads((HERE/'pins.json').read_text())
CAPS={
 'runtime':['listBuckets','readBuckets','listFiles','readFiles','writeFiles'],
 'reader':['listBuckets','readBuckets','listFiles','readFiles'],
 'archive-reader':['listBuckets','readBuckets','listFiles','readFiles','readFileRetentions'],
 'setup-lab':['listBuckets','readBuckets','listFiles','readFiles','writeFiles','deleteFiles','readFileRetentions'],
}
for _role in ('state-probe','archive-probe'):CAPS[_role]=list(CAPS['setup-lab'])


def digest(data):return hashlib.sha256(data).hexdigest()
def utc():return dt.datetime.now(dt.timezone.utc).isoformat()


def owned_dir(path):
    path=Path(path)
    if (not path.is_absolute() or not path.is_relative_to(BASE) or path==BASE
            or '..' in path.parts or path.resolve()!=path):
        raise ValueError('outside_owned_lab_root')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.resolve()!=path or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:
        raise ValueError('unsafe_lab_directory')
    return path


def private_write(path,data):
    path=Path(path);owned_dir(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())


def stored(entry,lab=False):
    if lab and (not entry.startswith(PASS_PREFIX) or len(entry.split('/'))!=3):
        raise ValueError('unexpected_custody_entry')
    env=dict(os.environ)
    if lab:env['PASSWORD_STORE_DIR']=str(PASS_STORE)
    p=subprocess.run(['pass','show',entry],capture_output=True,env=env,timeout=35)
    if p.returncode:raise ValueError('encrypted_custody_unavailable')
    return p.stdout


def store(name,value):
    if not re.fullmatch(r'[a-z-]+',name):raise ValueError('invalid_custody_name')
    entry=PASS_PREFIX+name;env=dict(os.environ,PASSWORD_STORE_DIR=str(PASS_STORE))
    target=PASS_STORE/(entry+'.gpg')
    if target.exists():
        previous=json.loads(stored(entry,True))
        if name!='fixture' or previous.get('run_id')!=value.get('run_id'):
            raise ValueError('existing_lab_custody_requires_review')
    p=subprocess.run(['pass','insert','--force','--multiline',entry],input=json.dumps(value).encode(),
                     env=env,capture_output=True,timeout=45)
    if p.returncode:raise ValueError('encrypted_custody_write_failed')
    if json.loads(stored(entry,True))!=value:raise ValueError('custody_readback_mismatch')
    p=subprocess.run(['git','-C',str(PASS_STORE),'push','origin',
                      'HEAD:refs/heads/codex/synthetic-tofu-backend-20261119'],
                     capture_output=True,timeout=60)
    if p.returncode:raise ValueError('private_custody_checkpoint_failed')


def wire():
    spec=importlib.util.spec_from_file_location('qualification_b2_wire',HERE.parent/'object-schedule/b2_wire.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def admin():
    original=json.loads(stored('rbx/object-backup/b2-writer'))
    auth=wire().authorize(original['accountId'],stored('rbx/backblaze/setup-admin').decode().strip())
    if auth['accountId']!=original['accountId']:raise ValueError('unexpected_setup_account')
    if not {'writeKeys','writeBuckets','readBucketRetentions','writeBucketRetentions'} <= set(wire().normalized(auth)['capabilities']):
        raise ValueError('setup_capabilities_missing')
    return auth


def api(auth,operation,payload):
    if operation not in ('b2_create_bucket','b2_update_bucket','b2_list_buckets','b2_create_key'):
        raise ValueError('unsupported_setup_operation')
    return wire().request(auth['apiInfo']['storageApi']['apiUrl']+'/b2api/v4/'+operation,
                          auth['authorizationToken'],payload)


def endpoint(value):
    p=urllib.parse.urlsplit(value)
    if (p.scheme!='https' or p.username is not None or p.password is not None or p.port not in (None,443)
        or p.path not in ('','/') or p.query or p.fragment
        or not re.fullmatch(r's3\.[a-z0-9-]+\.backblazeb2\.com',p.hostname or '')):
        raise ValueError('unexpected_tls_s3_endpoint')
    return value.rstrip('/')


def admit(binding):
    if not re.fullmatch(r'20261119-[0-9a-f]{8}',binding['run_id']):raise ValueError('unexpected_run_id')
    expected='qualification/v1/'+binding['run_id']+'/'
    if binding['prefix']!=expected:raise ValueError('unexpected_fixture_prefix')
    for role in ('state','archive'):
        bucket=binding['buckets'][role]
        if bucket['bucketName']!='rbx-tofu-'+role+'-'+binding['run_id']:
            raise ValueError('unexpected_fixture_bucket')
        if not bucket['bucketId']:raise ValueError('missing_fixture_bucket_id')
    if binding['buckets']['state']['bucketId']==binding['buckets']['archive']['bucketId']:
        raise ValueError('same_backend_and_archive_bucket')
    endpoint(binding['endpoint'])
    return binding


def setup(run_id):
    if not re.fullmatch(r'20261119-[0-9a-f]{8}',run_id):raise ValueError('invalid_run_id')
    entry=PASS_STORE/(PASS_PREFIX+'fixture.gpg')
    if entry.exists():
        value=admit(json.loads(stored(PASS_PREFIX+'fixture',True)))
        if value['run_id']!=run_id or not value.get('setup_complete'):raise ValueError('existing_setup_requires_review')
        return value
    auth=admin();binding={'run_id':run_id,'prefix':'qualification/v1/'+run_id+'/',
       'endpoint':endpoint(auth['apiInfo']['storageApi']['s3ApiUrl']),'account_id':auth['accountId'],
       'buckets':{},'created_at_utc':utc(),'setup_complete':False}
    # Persist the proposed binding before any resource mutation; partial setup is not silently retried.
    store('fixture',binding)
    for role in ('state','archive'):
        name='rbx-tofu-'+role+'-'+run_id
        payload={'accountId':auth['accountId'],'bucketName':name,'bucketType':'allPrivate',
          'bucketInfo':{'rbx-purpose':'synthetic-opentofu-qualification','rbx-run':run_id},
          'lifecycleRules':[], 'fileLockEnabled':role=='archive',
          'defaultServerSideEncryption':{'mode':'SSE-B2','algorithm':'AES256'}}
        made=api(auth,'b2_create_bucket',payload)
        binding['buckets'][role]={'bucketId':made['bucketId'],'bucketName':made['bucketName']}
        store('fixture',binding)
        if role=='archive':
            made=api(auth,'b2_update_bucket',{'accountId':auth['accountId'],'bucketId':made['bucketId'],
                'ifRevisionIs':made['revision'],'defaultRetention':{'mode':'compliance','period':{'duration':1,'unit':'days'}}})
        sse=made['defaultServerSideEncryption']['value']
        lock=made['fileLockConfiguration']['value']
        if sse!={'mode':'SSE-B2','algorithm':'AES256'} or made['bucketType']!='allPrivate' or made['lifecycleRules']:
            raise ValueError('bucket_admission_failed')
        if role=='state' and lock.get('defaultRetention',{}).get('mode') is not None:
            raise ValueError('mutable_lock_default_retention')
        if role=='archive' and (not lock['isFileLockEnabled'] or lock['defaultRetention']!={'mode':'compliance','period':{'duration':1,'unit':'days'}}):
            raise ValueError('archive_retention_admission_failed')
    admit(binding)
    for role,caps in CAPS.items():
        buckets,prefix=scope(binding,role)
        made=api(auth,'b2_create_key',{'accountId':auth['accountId'],'keyName':'rbx-tofu-'+role+'-'+run_id,
           'capabilities':caps,'bucketIds':buckets,'namePrefix':prefix,'validDurationInSeconds':86400 if role in ('setup-lab','state-probe','archive-probe') else 7*86400})
        if set(made['capabilities'])!=set(caps) or made['bucketIds']!=buckets or made['namePrefix']!=prefix:
            raise ValueError('scoped_identity_admission_failed')
        verified=wire().authorize(made['applicationKeyId'],made['applicationKey'])
        actual=wire().normalized(verified)
        if verified['accountId']!=auth['accountId'] or set(actual['capabilities'])!=set(caps) or actual['bucketIds']!=buckets or actual['namePrefix']!=prefix:
            raise ValueError('authorized_identity_scope_mismatch')
        store(role,made)
    store('encryption',{'passphrase':secrets.token_urlsafe(48),'created_at_utc':utc(),
                        'purpose':'lab-only OpenTofu state encryption; independent of B2'})
    binding['setup_complete']=True;store('fixture',binding)
    return binding


def load():return admit(json.loads(stored(PASS_PREFIX+'fixture',True)))


def scope(binding,role):
    if role not in CAPS:raise ValueError('unexpected_runtime_role')
    if role=='setup-lab':
        return [binding['buckets'][x]['bucketId'] for x in ('state','archive')],binding['prefix']
    if role in ('state-probe','archive-probe'):
        return [binding['buckets'][role.split('-')[0]]['bucketId']],binding['prefix']
    bucket=binding['buckets']['archive' if role=='archive-reader' else 'state']['bucketId']
    return [bucket],binding['prefix']+('snapshots/' if role=='archive-reader' else 'active/')


def client(binding,role,sdk_dir):
    admit(binding)
    sys.path.insert(0,str(sdk_dir))
    import botocore.session
    from botocore.config import Config
    if role not in CAPS:raise ValueError('unexpected_runtime_role')
    key=json.loads(stored(PASS_PREFIX+role,True));buckets,prefix=scope(binding,role)
    if set(key['capabilities'])!=set(CAPS[role]) or key['bucketIds']!=buckets or key['namePrefix']!=prefix:
        raise ValueError('runtime_identity_scope_drift')
    api=botocore.session.get_session().create_client('s3',endpoint_url=binding['endpoint'],
        region_name=urllib.parse.urlsplit(binding['endpoint']).hostname.split('.')[1],
        aws_access_key_id=key['applicationKeyId'],aws_secret_access_key=key['applicationKey'],verify=True,
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=5,read_timeout=15,
            retries={'max_attempts':0},request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    def check_destination(request,**kwargs):
        p=urllib.parse.urlsplit(request.url)
        if p.scheme!='https' or p.netloc!=urllib.parse.urlsplit(binding['endpoint']).netloc:
            raise ValueError('redirect_or_provider_destination_refused')
    api.meta.events.register('before-send.s3',check_destination)
    return api


def safe_call(function,**kwargs):
    try:
        response=function(**kwargs)
        return {'ok':True,'http':response.get('ResponseMetadata',{}).get('HTTPStatusCode'),
                'version_id':response.get('VersionId'),'sse':response.get('ServerSideEncryption'),
                'delete_marker':response.get('DeleteMarker')},response
    except Exception as e:
        response=getattr(e,'response',None)
        if not isinstance(response,dict):response={}
        code=response.get('Error',{}).get('Code',type(e).__name__)
        return {'ok':False,'http':response.get('ResponseMetadata',{}).get('HTTPStatusCode'),
                'error_code':code if re.fullmatch('[A-Za-z0-9_]{1,64}',str(code)) else 'provider_error'},None
