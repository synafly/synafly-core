#!/usr/bin/env python3
"""Offline three-process mesh verification; --live explicitly opts into bounded BSC reads."""
import argparse
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.rpc import BSC_GENESIS, NetworkIdentity, encode, load_json
from synafly_lab.edge_daemon import DaemonOrigin, EdgeDaemon, DEFAULT_UPSTREAM
from synafly_lab.edge_mesh import load_topology
ROOT=Path(__file__).resolve().parents[1]


def call(port,method,params):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
    try:
        connection.request('POST','/',encode({'jsonrpc':'2.0','id':1,'method':method,'params':params}),{'Content-Type':'application/json'})
        response=connection.getresponse();value=load_json(response.read())
        if response.status!=200 or 'result' not in value:raise ValueError('Daemon verification RPC failed')
        return value['result']
    finally:connection.close()


class Process:
    def __init__(self,args,report,env=None):
        self.report=report
        self.process=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_edge_daemon.py'),*args,'--report',str(report)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,env=env)
        # Bounded startup: CLI logs are consumed on a reader thread, never wait forever.
        import queue,threading
        lines=queue.Queue(maxsize=1)
        threading.Thread(target=lambda:lines.put(self.process.stderr.readline()),daemon=True).start()
        try:
            line=lines.get(timeout=10);match=re.search(r'Listening on http://127\.0\.0\.1:(\d+)',line)
            if not match:raise ValueError('Daemon startup failed; inspect local configuration')
            self.port=int(match[1])
        except Exception:self.close();raise
    def close(self):
        if self.process.poll() is None:self.process.send_signal(signal.SIGTERM)
        try:self.process.communicate(timeout=15)
        except subprocess.TimeoutExpired:self.process.kill();self.process.communicate();raise
        if self.process.returncode!=0:raise ValueError('Daemon exit failure')
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def offline():
    import os,socket
    sys.path.insert(0,str(ROOT/'tests'))
    from daemon_fixture import WireOrigin, IDENTITY, ADDRESS, PIN, TOKEN, catalog_document, SELECTOR
    topology=load_topology(ROOT/'data/mesh-topology.json');neighbor=topology[0][0]
    with tempfile.TemporaryDirectory() as directory, WireOrigin() as wire, ExitStack() as stack:
        directory=Path(directory)
        # OS-selected peer port, checked by the child bind; no silent port fallback.
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));peer_port=sock.getsockname()[1]
        env={**os.environ,'SYNAFLY_MESH_TOKEN':TOKEN};env.pop('SYNAFLY_RPC_TOKEN',None)
        common=['--port','0','--upstream',wire.url,'--chain-id','1337','--genesis-hash',IDENTITY.genesis_hash]
        b=stack.enter_context(Process([*common,'--peer-port',str(peer_port),'--role',str(neighbor)],directory/'b.json',env))
        warm=call(b.port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])
        a=stack.enter_context(Process([*common,'--peer-port','0','--role','0','--peers',f'{neighbor}=http://127.0.0.1:{peer_port}'],directory/'a.json',env))
        before=wire.counts['eth_getStorageAt'];value=call(a.port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])
        after=wire.counts['eth_getStorageAt']
        if value!=warm or after!=before:raise ValueError('Mesh did not reuse peer state')
        b.close()
        fallback=call(a.port,'eth_getStorageAt',[ADDRESS,'0x9',PIN])
        a.close();mesh_report=json.loads((directory/'a.json').read_text())
        catalog=directory/'catalog.json';catalog.write_bytes(encode(catalog_document()))
        wire.transactions=[{'to':ADDRESS,'input':SELECTOR}]
        c=stack.enter_context(Process([*common,'--recipe-catalog',str(catalog),'--watch-blocks','--watch-interval','5'],directory/'c.json',env))
        deadline=time.monotonic()+5
        while wire.counts['eth_getStorageAt']<after+2:
            if time.monotonic()>deadline:raise ValueError('Warmer did not prefetch')
            time.sleep(.01)
        # Poll its metrics so an origin response in transit cannot race the client read.
        while True:
            con=http.client.HTTPConnection('127.0.0.1',c.port,timeout=2);con.request('GET','/metrics');metrics=load_json(con.getresponse().read());con.close()
            if metrics['prefetch'].get('slot_reads_completed',0)>0:break
            if time.monotonic()>deadline:raise ValueError('Warm cache not ready')
            time.sleep(.01)
        before=wire.counts['eth_getStorageAt'];prefetched=call(c.port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])
        if wire.counts['eth_getStorageAt']!=before or prefetched!=warm:raise ValueError('Proactive cache miss')
        c.close();warm_report=json.loads((directory/'c.json').read_text())
        return {'schema':'synafly.daemon-verification.v1','mode':'offline-owned-wire',
                'independent_daemon_processes':3,'simultaneous_mesh_processes':2,
                'mesh_peer_hit':mesh_report['mesh']['hits']==1,'peer_exit_fallback':mesh_report['mesh']['failures']>=1,
                'foreground_parity':value==warm==prefetched,'proactive_before_first_client':True,
                'mesh_metrics':mesh_report,'warmer_metrics':warm_report,
                'scope':'Loopback processes, static trusted role peers, synthetic slot-8 catalog; not public WAN or PancakeSwap training'}


def live(upstream):
    origin=DaemonOrigin(upstream,NetworkIdentity(56,BSC_GENESIS),timeout=5)
    daemon=EdgeDaemon(origin)
    try:
        header=daemon.read('eth_getBlockByNumber',['latest',False])
        pin={'blockHash':header['hash'],'requireCanonical':False}
        address='0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae'
        # Eight bounded reads, no tx, no trace, no mempool, no retries or prefetch.
        values=[daemon.read('eth_getStorageAt',[address,'0x8',pin]) for _ in range(8)]
        with origin.lane('verification'):reference=origin.call('eth_getStorageAt',[address,'0x8',pin])
        if any(value!=reference for value in values):raise ValueError('Public read parity failed')
        return {'schema':'synafly.daemon-live.v1','mode':'explicit-public-read-only','block_hash':header['hash'],
                'parity':True,'logical_storage_reads':8,'metrics':daemon.metrics(),
                'scope':'Sequential pinned cache check; no mesh/prediction/cost claim; not a consensus proof'}
    finally:daemon.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true')
    p.add_argument('--upstream',default=DEFAULT_UPSTREAM);p.add_argument('--out',default='.local/edge-daemon-verification.json');args=p.parse_args()
    value=live(args.upstream) if args.live else offline()
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(value)+b'\n')
    print(json.dumps({'mode':value['mode'],'verification':'passed'}))
if __name__=='__main__':main()
