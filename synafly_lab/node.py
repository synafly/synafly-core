"""An independent keeper/verifier process. Local-only by default; not a BSC validator."""
import argparse
import hmac
import json
import os
from pathlib import Path
import re
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
from .canonical import Invalid,canonical,parse
from .checkpoint import Run
from .model import stimulus
from .peer import origin,sync
from .store import Store,Conflict

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(16);super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):request.close();return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,*args):
        try:super().process_request_thread(*args)
        finally:self.slots.release()

def serve(store,node_id,peers,admin_token,host='127.0.0.1',port=0):
    if len(admin_token)<24:raise Invalid('Set LAB_ADMIN_TOKEN to at least 24 characters')
    if not re.fullmatch('[A-Za-z0-9_-]{1,40}',node_id):raise Invalid('Node id')
    peers={k:origin(v) for k,v in peers.items()}
    if len(peers)>16:raise Invalid('Peer limit')
    class Handler(BaseHTTPRequestHandler):
        def setup(self):super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass
        def reply(self,value,status=200):
            raw=canonical(value);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path=='/health':return self.reply({'status':'ok','node_id':node_id,'run_id':store.run.id})
                if path=='/v1/spec':return self.reply(store.run.spec)
                if path=='/v1/head':
                    h=store.head();p=h['checkpoint'];return self.reply({'hash':h['hash'],'sequence':p['sequence'],'tick':p['tick'],'run_id':store.run.id,'node_id':node_id})
                if re.fullmatch('/v1/checkpoints/[0-9a-f]{64}',path):
                    h=path.rsplit('/',1)[1];return self.reply({'hash':h,'checkpoint':store.get(h)})
                return self.reply({'error':'Not found'},404)
            except Invalid as e:self.reply({'error':str(e)},400)
            except (ConnectionError,TimeoutError):return
        def do_POST(self):
            try:
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+admin_token):return self.reply({'error':'Operator authorization required'},401)
                if self.headers.get_content_type()!='application/json' or self.headers.get('Transfer-Encoding'):return self.reply({'error':'JSON with Content-Length required'},415)
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=131072:return self.reply({'error':'Body limit'},413)
                body=parse(self.rfile.read(size));path=urlsplit(self.path).path
                if type(body) is not dict:raise Invalid('Object required')
                if path=='/v1/advance':
                    if set(body)!={'expected_parent','inputs'}:raise Invalid('Advance fields')
                    return self.reply(store.advance(body['inputs'],body['expected_parent']))
                if path=='/v1/sync':
                    if set(body)!={'peer'} or body['peer'] not in peers:raise Invalid('Unknown configured peer')
                    return self.reply(sync(store,peers[body['peer']]))
                return self.reply({'error':'Not found'},404)
            except Conflict as e:self.reply({'error':str(e)},409)
            except (Invalid,ValueError,TypeError,KeyError) as e:self.reply({'error':str(e)[:160]},400)
            except (ConnectionError,TimeoutError):return
    return BoundedServer((host,port),Handler)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--node-id',required=True);p.add_argument('--store',required=True);p.add_argument('--graph',default='data/malecns-sample.json');p.add_argument('--seed',default='synafly-research-v1');p.add_argument('--port',type=int,default=8821);p.add_argument('--host',default='127.0.0.1');p.add_argument('--allow-remote',action='store_true');p.add_argument('--peer',action='append',default=[]);a=p.parse_args()
    if a.host not in {'127.0.0.1','localhost','::1'} and not a.allow_remote:p.error('Remote serving requires explicit --allow-remote and an operator-managed TLS gateway')
    peers={}
    for entry in a.peer:
        k,sep,v=entry.partition('=')
        if not sep or k in peers:p.error('Use unique NAME=ORIGIN peers')
        peers[k]=v
    run=Run(parse(Path(a.graph).read_bytes()),a.seed);store=Store(a.store,run)
    server=serve(store,a.node_id,peers,os.getenv('LAB_ADMIN_TOKEN',''),a.host,a.port)
    print(json.dumps({'node_id':a.node_id,'port':server.server_port,'run_id':run.id,'mode':'research-keeper'}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();store.close()
if __name__=='__main__':main()
