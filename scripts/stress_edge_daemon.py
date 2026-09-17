#!/usr/bin/env python3
"""Owned loopback burst stress. Never point a high-load test at public RPC."""
import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from daemon_fixture import IDENTITY, ADDRESS, PIN, GENESIS, BLOCK
from synafly_lab.daemon_ingress import AsyncDaemonServer
from synafly_lab.rpc import encode,load_json
from verify_edge_daemon import Process


class OwnedAsyncOrigin:
    """Same bounded HTTP parser, 16 workers; synthetic 10 ms state-read service."""
    def __init__(self):
        self.identity=IDENTITY;self.mesh=None;self.counts=Counter();self.lock=threading.Lock()
        self.server=AsyncDaemonServer(self,0,capacity=5000)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever)
    def read(self,method,params):
        with self.lock:self.counts[method]+=1
        if method=='eth_chainId':return '0x539'
        if method=='eth_getBlockByNumber':return {'number':'0x0','hash':GENESIS}
        if method!='eth_getStorageAt':raise ValueError('Fixture method')
        time.sleep(.01)
        return '0x'+format(int(params[1],16),'064x')
    def __enter__(self):self.thread.start();self.server.ready.wait(5);return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join()


def percentile(values,p):
    values=sorted(values)
    return round(values[max(0,math.ceil(p*len(values))-1)]*1000,3) if values else None


async def burst(port,clients,kind):
    gate=asyncio.Event();counts=Counter();latencies=[];active=peak=0
    async def client(i):
        nonlocal active,peak
        await gate.wait();started=time.perf_counter();active+=1;peak=max(peak,active);writer=None
        try:
            reader,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',port),8)
            slot=hex(i) if kind=='unique' else '0x8'
            pin='latest' if kind=='latest' else PIN
            body=encode({'jsonrpc':'2.0','id':i,'method':'eth_getStorageAt','params':[ADDRESS,slot,pin]})
            writer.write(f'POST / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
            await writer.drain()
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),8)
            status=int(header.split(b' ')[1]);counts['http_'+str(status)]+=1
            headers=dict(line.split(b':',1) for line in header.split(b'\r\n')[1:] if b':' in line)
            raw=await asyncio.wait_for(reader.readexactly(int(headers.get(b'Content-Length',b'0'))),8)
            if status==200:
                value=load_json(raw)
                if value.get('id')!=i:counts['bad_id']+=1
                if value.get('result')=='0x'+format(int(slot,16),'064x'):
                    counts['correct']+=1;latencies.append(time.perf_counter()-started)
                elif 'error' in value:counts['rpc_errors']+=1
                else:counts['wrong_values']+=1
        except (OSError,TimeoutError,ValueError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):counts['transport_errors']+=1
        finally:
            active-=1
            if writer:
                writer.close()
                try:await writer.wait_closed()
                except OSError:pass
    tasks=[asyncio.create_task(client(i)) for i in range(clients)]
    gate.set();started=time.perf_counter();await asyncio.gather(*tasks)
    return {'attempted_clients':clients,'peak_client_tasks':peak,'wall_seconds':round(time.perf_counter()-started,6),
            'responses':dict(counts),'success_latency_ms':{name:percentile(latencies,p) for name,p in [('p50',.5),('p90',.9),('p99',.99)]},
            'scope':'Simultaneously released client tasks; not all are admitted or executing concurrently. Latency includes connection/admission wait; success-only percentiles.'}


def run(clients,kind,capacity=None):
    capacity=capacity or max(1024,clients)
    with tempfile.TemporaryDirectory() as temp, OwnedAsyncOrigin() as wire:
        direct=asyncio.run(burst(wire.server.server_port,clients,kind));direct_reads=wire.counts['eth_getStorageAt']
        report=Path(temp)/'report.json'
        with Process(['--port','0','--capacity',str(capacity),'--upstream',wire.url,'--chain-id','1337','--genesis-hash',IDENTITY.genesis_hash],report) as daemon:
            before=wire.counts['eth_getStorageAt'];edge=asyncio.run(burst(daemon.port,clients,kind))
        metrics=json.loads(report.read_text());edge_reads=wire.counts['eth_getStorageAt']-before
        if any(row['responses'].get(k,0) for row in (direct,edge) for k in ['wrong_values','bad_id']):raise ValueError('Parity failure')
        return {'workload':kind,'edge_capacity':capacity,'direct':direct,'edge':edge,'direct_origin_reads':direct_reads,'edge_origin_reads':edge_reads,
                'edge_metrics':metrics,'rejected_requests_counted_as_offload':False,
                'comparison':'Origin counts must be read with successful/admitted counts; unequal admission is not a cost-saving comparison.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--clients',type=int,nargs='+',default=[1000,5000]);p.add_argument('--out',default='.local/edge-daemon-stress.json');p.add_argument('--cooldown',type=int,default=65,help='Seconds between cases to avoid loopback TIME_WAIT exhaustion');args=p.parse_args()
    if any(not 1<=n<=5000 for n in args.clients):p.error('Clients 1..5000')
    if not 0<=args.cooldown<=120:p.error('Cooldown 0..120 seconds')
    cases=[(n,kind,None) for n in args.clients for kind in ['hot','unique','latest']]+[(max(args.clients),'hot',64)]
    rows=[]
    for i,(n,kind,capacity) in enumerate(cases):
        if i:time.sleep(args.cooldown)
        rows.append(run(n,kind,capacity))
        print(json.dumps({'completed':kind,'clients':n,'capacity':capacity or max(1024,n)}),flush=True)
    value={'schema':'synafly.daemon-stress.v1','mode':'owned-loopback-wire','origin_delay_ms':10,'origin_admission_capacity':5000,'edge_default_admission_capacity':1024,'rpc_workers':16,
           'cooldown_seconds':args.cooldown,'rows':rows,'limits':'No production traffic, WAN, physical disk I/O or billing inference. RSS is a process high-water mark, not proof of no memory leaks.'}
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(value)+b'\n')
    for row in rows:print(json.dumps({k:row[k] for k in ['workload','direct_origin_reads','edge_origin_reads']}))
if __name__=='__main__':main()
