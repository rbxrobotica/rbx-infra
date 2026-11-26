"""Bounded Native API transport. No pass, owner store or administrative actions."""
import base64
import json
import re
import urllib.parse
import urllib.request

ROOT='https://api.backblazeb2.com/b2api/v4/b2_authorize_account'
MAX_RESPONSE=1024*1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')


def destination(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
        raise ValueError('unsafe_api_destination')
    if not p.hostname or not (p.hostname=='api.backblazeb2.com' or p.hostname.endswith('.backblazeb2.com')):
        raise ValueError('unexpected_api_provider')
    return url


def upload_destination(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
        raise ValueError('unsafe_upload_destination')
    # Official Native upload pods use backblaze.com, not the API's backblazeb2.com.
    # https://www.backblaze.com/apidocs/b2-get-upload-url
    if not re.fullmatch(r'pod-\d{3}-\d{4}-\d{2}\.backblaze\.com',p.hostname or ''):
        raise ValueError('unexpected_upload_provider')
    # The service also returns the path-parameter form of the same upload route.
    if not re.fullmatch(r'/b2api/v4/b2_upload_file(?:/[A-Za-z0-9_-]{1,512}/[A-Za-z0-9_-]{1,512})?',p.path):
        raise ValueError('unexpected_upload_route')
    return url


def request(url,authorization,payload=None):
    destination(url)
    q=urllib.request.Request(url,headers={'Authorization':authorization,'Content-Type':'application/json'},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.build_opener(NoRedirect).open(q,timeout=20) as response:
        raw=response.read(MAX_RESPONSE+1)
    if len(raw)>MAX_RESPONSE:raise ValueError('oversized_api_response')
    return json.loads(raw)


def authorize(key_id,secret,transport=request):
    result=transport(ROOT,'Basic '+base64.b64encode((key_id+':'+secret).encode()).decode())
    info=result['apiInfo']['storageApi'];destination(info['apiUrl'])
    return result


def normalized(auth):
    allowed=auth['apiInfo']['storageApi']['allowed'];buckets=allowed.get('buckets')
    return {'capabilities':allowed['capabilities'],'bucketIds':[x['id'] for x in buckets] if buckets else None,
            'namePrefix':allowed.get('namePrefix')}
