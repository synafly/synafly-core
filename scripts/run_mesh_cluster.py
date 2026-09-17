#!/usr/bin/env python3
"""Three real localhost daemon processes with PR #5 role-neighbor connections."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.edge_mesh import load_topology
from synafly_lab.rpc import encode,load_json
ROOT=Path(__file__).resolve().parents[1]


def request(port,method=None,params=None,path='/'):
    con=http.client.HTTPConnection('127.0.0.1',port,timeout=8)
    try:
        if method is None:con.request('GET',path)
        else:con.request('POST',path,encode({'jsonrpc':'2.0','id':1,'method':method,'params':params or []}),{'Content-Type':'application/json'})
        response=con.getresponse();value=load_json(response.read(2097153),2097152)
        if response.status!=200 or method is not None and 'result' not in value:raise ValueError('Cluster HTTP/RPC request failed')
        return value if method is None else value['result']
    finally:con.close()


class MeshCluster:
    def __init__(self,upstream,chain_id,genesis,directory,ports=(0,0,0),catalog=None):
        if len(ports)!=3 or any(type(p) is not int or not 0<=p<=65535 for p in ports):raise ValueError('Three bounded ports')
        self.upstream,self.chain_id,self.genesis=upstream,chain_id,genesis
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.requested_ports=ports;self.catalog=catalog;self.processes=[];self.ports=[];self.peer_ports=[]
    def __enter__(self):
        sockets=[]
        try:
            for port in [*self.requested_ports,0,0,0]:
                sock=socket.socket();sockets.append(sock);sock.bind(('127.0.0.1',port))
            self.ports=[s.getsockname()[1] for s in sockets[:3]];self.peer_ports=[s.getsockname()[1] for s in sockets[3:]]
            topology=load_topology(ROOT/'data/mesh-topology.json')
            env={**os.environ,'SYNAFLY_MESH_TOKEN':secrets.token_urlsafe(32)};env.pop('SYNAFLY_RPC_TOKEN',None)
            for role in range(3):
                sockets[role].close();sockets[role+3].close()
                arguments=[sys.executable,str(ROOT/'scripts/run_edge_daemon.py'),'--port',str(self.ports[role]),
                    '--peer-port',str(self.peer_ports[role]),'--role',str(role),'--upstream',self.upstream,
                    '--chain-id',str(self.chain_id),'--genesis-hash',self.genesis,'--report',str(self.directory/f'node-{role}.json')]
                if self.catalog and role==0:arguments.extend(['--recipe-catalog',str(self.catalog)])
                arguments.extend(['--peers',*[f'{n}=http://127.0.0.1:{self.peer_ports[n]}' for n in topology[role] if n in range(3)]])
                self.processes.append(subprocess.Popen(arguments,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            deadline=time.monotonic()+10
            for process,port in zip(self.processes,self.ports):
                while True:
                    if process.poll() is not None:raise ValueError('Cluster daemon exited; check ports/configuration')
                    try:
                        if request(port,path='/health')['chain_id']!=self.chain_id:raise ValueError('Cluster identity mismatch')
                        break
                    except (OSError,http.client.HTTPException):
                        if time.monotonic()>deadline:raise ValueError('Cluster readiness timeout')
                        time.sleep(.02)
            return self
        except BaseException:self.close();raise
        finally:
            for sock in sockets:sock.close()
    def close(self):
        for process in self.processes:
            if process.poll() is None:process.terminate()
        for process in self.processes:
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait();raise
    def __exit__(self,*_):self.close()
    def demonstrate(self,address,pin):
        params=[address,'0x8',pin]
        warm=request(self.ports[1],'eth_getStorageAt',params)
        peers=[request(self.ports[i],'eth_getStorageAt',params) for i in (0,2)]
        if any(value!=warm for value in peers):raise ValueError('Peer parity mismatch')
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(lambda _:request(self.ports[0],'eth_getStorageAt',[address,'0x9',pin]),range(8)))
        if len(set(values))!=1:raise ValueError('Burst parity mismatch')
        for _ in range(4):request(self.ports[0],'eth_getStorageAt',params)
        snapshots=[request(port,path='/receipts') for port in self.ports]
        metrics=[request(port,path='/metrics') for port in self.ports]
        if metrics[0]['mesh'].get('hits',0)<1 or metrics[2]['mesh'].get('hits',0)<1:raise ValueError('Expected real neighbor reuse')
        return {'schema':'synafly.mesh-cluster.v1','daemon_processes':3,'roles':[0,1,2],
                'directed_arcs':[[0,1],[1,0],[1,2],[2,1]],'peer_values_match':True,
                'receipts':snapshots,'metrics':metrics,
                'scope':'Three processes on one localhost/operator; not geographical decentralization'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ports',type=int,nargs=3,default=[8545,8546,8547])
    p.add_argument('--demo',action='store_true',help='Exercise receipts then stop all owned processes')
    p.add_argument('--out',default='.local/mesh-cluster.json');args=p.parse_args()
    # Default is fully offline. The EVM verifier supplies its own freshly spawned Anvil.
    sys.path.insert(0,str(ROOT/'tests'))
    from daemon_fixture import WireOrigin,IDENTITY,ADDRESS,PIN
    with tempfile.TemporaryDirectory() as directory,WireOrigin(delay=.02) as origin,MeshCluster(origin.url,IDENTITY.chain_id,IDENTITY.genesis_hash,directory,args.ports) as cluster:
        report=cluster.demonstrate(ADDRESS,PIN)
        print(json.dumps({'endpoints':[f'http://127.0.0.1:{port}' for port in cluster.ports],'mode':'owned-offline-fixture','peer_reuse_verified':True}),flush=True)
        if not args.demo:
            print('Ctrl-C stops the three owned daemons and exports their receipts.',flush=True)
            try:
                while True:time.sleep(1)
            except KeyboardInterrupt:pass
            report['receipts']=[request(port,path='/receipts') for port in cluster.ports]
            report['metrics']=[request(port,path='/metrics') for port in cluster.ports]
        path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(report)+b'\n')
if __name__=='__main__':main()
