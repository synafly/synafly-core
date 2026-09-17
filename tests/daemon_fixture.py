"""Owned deterministic wire origin for daemon integration tests and local stress."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from synafly_lab.rpc import NetworkIdentity, encode, load_json

GENESIS='0x'+'01'*32
BLOCK='0x'+'11'*32
BLOCK_B='0x'+'22'*32
ADDRESS='0x'+'ab'*20
CODE='0x60085460005260206000f3'
SELECTOR='0x0902f1ac'
IDENTITY=NetworkIdentity(1337,GENESIS)
TOKEN='local-fixture-not-a-production-secret'
PIN={'blockHash':BLOCK,'requireCanonical':False}


class WireOrigin:
    def __init__(self,delay=0,server_class=ThreadingHTTPServer):
        self.counts=Counter();self.lock=threading.Lock();self.delay=delay
        self.gate=None;self.head=BLOCK;self.code=CODE;self.fault=None;self.transactions=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                value=load_json(self.rfile.read(int(self.headers['Content-Length'])))
                method,params=value['method'],value['params']
                with owner.lock:owner.counts[method]+=1
                if method in {'eth_getStorageAt','eth_call','eth_getCode'}:
                    if owner.gate:owner.gate.wait(5)
                    if owner.delay:time.sleep(owner.delay)
                if method=='eth_chainId':result='0x539'
                elif method=='eth_blockNumber':result='0x1'
                elif method=='eth_getBlockByNumber':result={'number':'0x0' if params[0]=='0x0' else '0x1','hash':GENESIS if params[0]=='0x0' else owner.head,'transactions':owner.transactions if params[1] else []}
                elif method=='eth_getCode':result=owner.code
                elif method=='eth_call':result='0x'+'08'.zfill(64)
                elif method=='eth_getStorageAt':result='0x'+format(int(params[1],16)+(100 if (params[-1].get('blockHash') if isinstance(params[-1],dict) else owner.head)==BLOCK_B else 0),'064x')
                else:result=None
                response={'jsonrpc':'2.0','id':value['id'],'result':result}
                if owner.fault=='malformed' and method=='eth_getStorageAt':response['result']='invalid'
                if owner.fault=='error' and method in {'eth_call','eth_getStorageAt'}:response={'jsonrpc':'2.0','id':value['id'],'error':{'code':3,'message':'execution reverted'}}
                if isinstance(params,list) and params and isinstance(params[-1],dict) and params[-1].get('requireCanonical') and params[-1].get('blockHash')!=owner.head:
                    response={'jsonrpc':'2.0','id':value['id'],'error':{'code':-32000,'message':'Noncanonical'}}
                raw=encode(response);self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except OSError:pass
        self.server=server_class(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01})
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):
        if self.gate:self.gate.set()
        self.server.shutdown();self.server.server_close();self.thread.join()


def catalog_document():
    from synafly_lab.keccak import keccak256
    return {'schema':'synafly.daemon-recipes.v1','chain_id':1337,'genesis_hash':GENESIS,'entries':[
        {'code_hash':'0x'+keccak256(bytes.fromhex(CODE[2:])).hex(),'selector':SELECTOR,'data':SELECTOR,
         'caller':'0x'+'00'*20,'value':'0x0','recipe':{'accesses':[['sload',['const',8]]],'guards':[]}}]}
