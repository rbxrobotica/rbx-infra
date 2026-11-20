"""Separate native-Ceph-only admission; does not weaken the old B2 boundary."""
import base64,hashlib,json,os,re,secrets,subprocess,sys,urllib.parse
from pathlib import Path
import core,engine

PREFIX='rbx/tofu-infom/'
ENDPOINTS={r:'https://s3.'+r+'.infomaniak.cloud' for r in ('gva1','gva2')}


def admit(b):
    if not re.fullmatch(r'20261120-[0-9a-f]{8}',b['run_id']):raise ValueError('unexpected_infomaniak_run')
    if b.get('provider')!='infomaniak-native-ceph':raise ValueError('legacy_or_unknown_provider')
    if b.get('region') not in ENDPOINTS or b.get('endpoint')!=ENDPOINTS[b['region']]:raise ValueError('unexpected_infomaniak_endpoint')
    if not re.fullmatch(r'[a-f0-9]{32}',b.get('tenant_id','')):raise ValueError('invalid_fixture_tenant')
    if b.get('project_name')!='rbx-tofu-qual-'+b['run_id'] or b.get('dedicated_synthetic_project_verified') is not True:
        raise ValueError('dedicated_project_not_verified')
    if b.get('bucket')!='rbx-tofu-ik-'+b['run_id'] or b.get('prefix')!='qualification/v2/'+b['run_id']+'/':
        raise ValueError('unexpected_infomaniak_namespace')
    return b


def read(name):
    if name not in ('fixture','runtime','encryption','sse-c'):raise ValueError('unexpected_infomaniak_entry')
    p=subprocess.run(['pass','show',PREFIX+name],capture_output=True,
        env=dict(os.environ,PASSWORD_STORE_DIR=str(core.PASS_STORE)),timeout=35)
    if p.returncode:raise ValueError('infomaniak_custody_unavailable')
    return json.loads(p.stdout)


def store(name,value):
    if name not in ('fixture','runtime','encryption','sse-c'):raise ValueError('unexpected_infomaniak_entry')
    target=core.PASS_STORE/(PREFIX+name+'.gpg')
    if target.exists():
        previous=read(name)
        if name!='fixture' or previous.get('run_id')!=value.get('run_id'):
            raise ValueError('preserve_existing_infomaniak_entry')
    env=dict(os.environ,PASSWORD_STORE_DIR=str(core.PASS_STORE))
    p=subprocess.run(['pass','insert','--force','--multiline',PREFIX+name],input=json.dumps(value).encode(),
        capture_output=True,env=env,timeout=45)
    if p.returncode or read(name)!=value:raise ValueError('infomaniak_custody_write_failed')
    p=subprocess.run(['git','-C',str(core.PASS_STORE),'push','origin',
        'HEAD:refs/heads/codex/synthetic-tofu-backend-20261119'],capture_output=True,timeout=60)
    if p.returncode:raise ValueError('infomaniak_custody_not_remote')


def load():return admit(read('fixture'))


def runtime(b):
    admit(b);key=read('runtime')
    if key.get('run_id')!=b['run_id'] or key.get('tenant_id')!=b['tenant_id']:
        raise ValueError('infomaniak_runtime_scope_mismatch')
    if not all(isinstance(key.get(x),str) and 16<=len(key[x])<=256 for x in ('access_key','secret_key')):
        raise ValueError('invalid_infomaniak_runtime_format')
    return key


def sse_key(b):
    value=read('sse-c')
    if value.get('run_id')!=b['run_id']:raise ValueError('sse_key_wrong_fixture')
    key=base64.b64decode(value['key_base64'],validate=True)
    if len(key)!=32:raise ValueError('invalid_sse_customer_key')
    return key


def sse(b):
    key=sse_key(b)
    return {'SSECustomerAlgorithm':'AES256','SSECustomerKey':base64.b64encode(key).decode(),
       'SSECustomerKeyMD5':base64.b64encode(hashlib.md5(key,usedforsecurity=False).digest()).decode()}


class Budget:
    def __init__(self):self.mutating=0;self.reads=0;self.uploaded_bytes=0
    def reserve(self,method,size=0):
        if method not in ('GET','HEAD','PUT','POST','DELETE') or type(size) is not int or size<0:
            raise ValueError('invalid_synthetic_request_budget')
        read=method in ('GET','HEAD');name='reads' if read else 'mutating'
        count=getattr(self,name)+1
        if count>(1000 if read else 200) or self.uploaded_bytes+size>1048576:
            raise ValueError('synthetic_request_budget_exceeded')
        setattr(self,name,count);self.uploaded_bytes+=size


def client(b,sdk_dir,budget=None,mode='runtime'):
    admit(b);key=runtime(b);budget=budget or Budget();sys.path.insert(0,str(sdk_dir))
    if mode not in ('setup','runtime'):raise ValueError('unexpected_synthetic_client_mode')
    import botocore.session
    from botocore.config import Config
    s3=botocore.session.get_session().create_client('s3',endpoint_url=b['endpoint'],region_name=b['region'],
       aws_access_key_id=key['access_key'],aws_secret_access_key=key['secret_key'],verify=True,
       config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':0},
          connect_timeout=5,read_timeout=15,request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    operations={'GetObject','HeadObject','ListObjectVersions','ListObjectsV2','PutObject','DeleteObject',
        'GetBucketVersioning','GetBucketLocation'}
    if mode=='setup':operations|={'ListBuckets','CreateBucket','PutBucketVersioning','PutBucketTagging','GetBucketAcl','GetBucketLifecycleConfiguration'}
    def operation(model,params,**kwargs):
        if model.name not in operations:raise ValueError('unreviewed_infomaniak_operation')
        if model.name=='ListBuckets':return
        if params.get('Bucket')!=b['bucket']:raise ValueError('other_infomaniak_bucket_refused')
        if model.name in ('ListObjectVersions','ListObjectsV2'):
            prefix=params.get('Prefix','')
            if (not prefix.startswith(b['prefix']) or '..' in prefix.split('/') or '.' in prefix.split('/')
                or not 1<=params.get('MaxKeys',100)<=100):raise ValueError('unscoped_infomaniak_listing')
        if model.name=='CreateBucket' and (set(params)-{'Bucket','CreateBucketConfiguration'} or
            params.get('CreateBucketConfiguration')!={'LocationConstraint':b['region']}):
            raise ValueError('unreviewed_bucket_creation')
        key=params.get('Key')
        if key is not None:
            if (not key.startswith(b['prefix']) or not re.fullmatch(r'[A-Za-z0-9_./-]+',key)
                or any(x in ('.','..','') for x in key.split('/'))):raise ValueError('other_infomaniak_prefix_refused')
        if model.name=='PutObject':
            forbidden={'ACL','GrantFullControl','GrantRead','GrantReadACP','GrantWriteACP','ObjectLockMode',
                       'ObjectLockRetainUntilDate','ObjectLockLegalHoldStatus','ServerSideEncryption','SSEKMSKeyId'}
            if forbidden&set(params) or params.get('SSECustomerAlgorithm')!='AES256':raise ValueError('unsafe_synthetic_upload')
            body=params.get('Body')
            if (type(body) is not bytes or len(body)>1048576 or
                params.get('ContentLength',len(body))!=len(body)):
                raise ValueError('unbounded_synthetic_upload')
        if model.name=='DeleteObject':
            if params.get('VersionId') or not (key==b['prefix']+'active/state.tfstate.tflock' or key.startswith(b['prefix']+'probes/')):
                raise ValueError('synthetic_delete_refused')
        if model.name=='PutBucketVersioning' and params.get('VersioningConfiguration')!={'Status':'Enabled'}:
            raise ValueError('versioning_downgrade_refused')
    def destination(request,**kwargs):
        p=urllib.parse.urlsplit(request.url)
        if p.scheme!='https' or p.netloc!=urllib.parse.urlsplit(b['endpoint']).netloc or p.query and 'X-Amz-' in p.query:
            raise ValueError('infomaniak_request_destination_refused')
        parts=urllib.parse.unquote(p.path).lstrip('/').split('/',1)
        if any(x in ('.','..') for x in urllib.parse.unquote(p.path).split('/')):raise ValueError('ambiguous_infomaniak_path')
        if parts[0] and parts[0]!=b['bucket']:raise ValueError('other_infomaniak_bucket_refused')
        if len(parts)>1 and parts[1] and not parts[1].startswith(b['prefix']):raise ValueError('other_infomaniak_prefix_refused')
        query={k for k,v in urllib.parse.parse_qsl(p.query,keep_blank_values=True)}
        if query-{'acl','location','tagging','versioning','lifecycle','versions','versionId','prefix','delimiter','max-keys','encoding-type','list-type','x-id'}:
            raise ValueError('unreviewed_infomaniak_subresource')
        if request.method in ('PUT','POST') and 'Content-Length' not in request.headers:
            raise ValueError('unknown_synthetic_content_length')
        length=int(request.headers.get('Content-Length',0)) if request.method in ('PUT','POST') else 0
        budget.reserve(request.method,length)
    s3.meta.events.register('before-parameter-build.s3',operation)
    s3.meta.events.register('before-send.s3',destination)
    return s3


def configure(b,root,name,unavailable=False):
    admit(b)
    if unavailable and name not in ('offline','unavailable'):raise ValueError('unreviewed_infomaniak_endpoint_override')
    backend={'bucket':b['bucket'],'key':b['prefix']+'active/state.tfstate',
      'workspace_key_prefix':b['prefix']+'active/workspaces','region':b['region'],
      'endpoints':{'s3':'https://127.0.0.1:1' if unavailable else b['endpoint']},
      'use_path_style':True,'encrypt':True,'use_lockfile':True,'skip_credentials_validation':True,
      'skip_region_validation':True,'skip_requesting_account_id':True,'skip_metadata_api_check':True,
      'skip_s3_checksum':True,'retry_mode':'standard','max_retries':0}
    return engine.configure_backend(b['run_id'],root,name,{'s3':backend})


def environment(b,working,generation,crypto='correct',hold=False,cloud=True,bad_credentials=False):
    admit(b);value=read('encryption')
    if value.get('run_id')!=b['run_id']:raise ValueError('encryption_key_wrong_fixture')
    env=engine.environment_base(working,generation,value['passphrase'],crypto,hold)
    env.update({'AWS_EC2_METADATA_DISABLED':'true','AWS_CONFIG_FILE':'/dev/null','AWS_SHARED_CREDENTIALS_FILE':'/dev/null',
        'AWS_MAX_ATTEMPTS':'1'})
    if cloud:
        key=runtime(b);env.update({'AWS_ACCESS_KEY_ID':key['access_key'],
          'AWS_SECRET_ACCESS_KEY':'invalid-synthetic-credential' if bad_credentials else key['secret_key'],
          'AWS_SSE_CUSTOMER_KEY':base64.b64encode(sse_key(b)).decode()})
    return env


def initialize_keys(b):
    admit(b)
    store('encryption',{'run_id':b['run_id'],'passphrase':secrets.token_urlsafe(48),'purpose':'regenerable synthetic fixture only'})
    store('sse-c',{'run_id':b['run_id'],'key_base64':base64.b64encode(secrets.token_bytes(32)).decode()})
