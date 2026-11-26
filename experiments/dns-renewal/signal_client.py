"""Per-check heartbeat transport; no redirects or secret-bearing diagnostics."""
import re
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_rejected')

def heartbeat(endpoint,failed,summary):
    if not re.fullmatch(r'https://hc-ping\.com/[0-9a-f-]{36}',endpoint):
        raise ValueError('unexpected_signal_destination')
    request=urllib.request.Request(endpoint+('/fail' if failed else ''),
                                   data=summary.encode('ascii'),method='POST')
    with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
        if response.status!=200 or response.read(32).strip()!=b'OK':
            raise ValueError('signal_not_confirmed')
