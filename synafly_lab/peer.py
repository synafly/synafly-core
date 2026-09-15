"""Explicit peer allowlists, bounded pulls and no redirect following."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .canonical import Invalid,parse,digest
from .store import Conflict

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def origin(url):
    u=urlsplit(url)
    if u.scheme not in {'https','http'} or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in {'','/'}:raise Invalid('Peer must be a bare origin')
    if u.scheme=='http' and u.hostname not in {'127.0.0.1','localhost','::1'}:raise Invalid('Remote peers require HTTPS')
    return url.rstrip('/')

def fetch(url):
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(url,timeout=5) as r:
            if r.status!=200:raise Invalid('Peer status')
            raw=r.read(1024*1024+1)
            if len(raw)>1024*1024:raise Invalid('Peer response too large')
            return parse(raw)
    except urllib.error.HTTPError as e:
        e.close();raise Invalid('Peer unavailable') from e
    except (urllib.error.URLError,TimeoutError) as e:raise Invalid('Peer unavailable') from e

def sync(store,url):
    url=origin(url);remote=fetch(url+'/v1/head');local=store.head()
    if type(remote) is not dict or remote.get('run_id')!=store.run.id:raise Invalid('Peer run mismatch')
    h=remote.get('hash')
    if type(h) is not str or not re.fullmatch('[0-9a-f]{64}',h):raise Invalid('Peer head hash')
    if h==local['hash']:return {'status':'up-to-date','hash':h,'imported':0}
    chain=[]
    for _ in range(256):
        if store.contains(h):
            if h!=local['hash']:raise Conflict('Peer is stale or forked; local head retained')
            break
        env=fetch(url+'/v1/checkpoints/'+h)
        if type(env) is not dict or set(env)!={'hash','checkpoint'} or env['hash']!=h or digest(env['checkpoint'])!=h:raise Invalid('Peer returned wrong content')
        chain.append(env);h=env['checkpoint'].get('parent')
        if type(h) is not str or not re.fullmatch('[0-9a-f]{64}',h):raise Invalid('Peer parent')
    else:raise Invalid('Peer chain exceeds sync bound')
    result=store.import_chain(list(reversed(chain)));return {'status':'fast-forward',**result}
