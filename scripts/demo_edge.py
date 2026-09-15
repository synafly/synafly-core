#!/usr/bin/env python3
"""Real local HTTP/process experiment; synthetic workload, no blockchain writes."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from synafly_lab.rpc import encode
from rpc_fixture import ADDRESS,BLOCK_A,BLOCK_B,BLOCK_MISSING,GENESIS,OriginFixture,state_value

def fetch(base,path,body=None):
    request=urllib.request.Request(base+path,data=encode(body) if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    with urllib.request.urlopen(request,timeout=6) as response:return json.load(response)

def run_mode(name,cache,coalesce):
    with OriginFixture() as fixture:
        command=[sys.executable,'-m','synafly_lab.edge_server','--upstream',fixture.url,
                 '--chain-id','1337','--genesis-hash',GENESIS,'--port','0',
                 '--cache-entries','128' if cache else '0']
        if not coalesce:command.append('--no-coalescing')
        proc=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        ready=queue.Queue()
        reader=threading.Thread(target=lambda:ready.put(proc.stdout.readline()),daemon=True);reader.start()
        try:
            line=ready.get(timeout=8)
            if not line:raise RuntimeError('Edge process exited before readiness')
            port=json.loads(line)['port'];base='http://127.0.0.1:'+str(port)
            health=fetch(base,'/health');assert health['chain_id']==1337
            outcomes=[]
            def read(index,selector):
                parameters=[ADDRESS,selector]
                body={'jsonrpc':'2.0','id':index,'method':'eth_getBalance','params':parameters}
                response=fetch(base,'/rpc',body);assert response['id']==index
                if isinstance(selector,dict):
                    block=selector['blockHash']
                    error=-32001 if block==BLOCK_MISSING else (-32000 if selector.get('requireCanonical') and block!=fixture.head else None)
                else:block=fixture.head;error=None
                if error is not None:assert response.get('error',{}).get('code')==error
                else:assert response.get('result')==state_value('eth_getBalance',parameters,block)
                return response
            # Hold the origin until all eight client requests are known to be active.
            # This measures an explicit simultaneous-miss scenario, not lucky timing.
            fixture.pause();selector={'blockHash':BLOCK_A,'requireCanonical':False}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                jobs=[pool.submit(read,i,selector) for i in range(8)]
                fixture.wait_reads(1 if coalesce else 8)
                if coalesce:
                    deadline=time.monotonic()+2
                    while True:
                        waiting=fetch(base,'/metrics')['edge']['coalesced_waiters']
                        if waiting==7:break
                        if time.monotonic()>deadline:raise AssertionError('Followers did not join the flight')
                        time.sleep(.002)
                fixture.release();outcomes.extend(job.result(timeout=6) for job in jobs)
            for index in range(8,16):outcomes.append(read(index,selector))
            for index in range(16,24):outcomes.append(read(index,{'blockHash':BLOCK_B,'requireCanonical':False}))
            for index in range(24,32):
                fixture.head=BLOCK_A if index<28 else BLOCK_B
                outcomes.append(read(index,'latest'))
            for index in range(32,36):outcomes.append(read(index,{'blockHash':BLOCK_A,'requireCanonical':True}))
            for index in range(36,38):outcomes.append(read(index,{'blockHash':BLOCK_MISSING,'requireCanonical':False}))
            measured=fetch(base,'/metrics')
            assert measured['origin']['state_reads']==fixture.reads
            assert measured['origin']['metadata_calls']==fixture.metadata==2
            assert measured['edge']['read_requests']==38
            return {'mode':name,'cache_enabled':cache,'coalescing_enabled':coalesce,
                    'client_requests':len(outcomes),'outcomes_sha256':hashlib.sha256(encode(outcomes)).hexdigest(),
                    'all_expected_responses_match':True,'measured':measured,
                    'fixture_peak_active_reads':fixture.peak}
        finally:
            fixture.release();proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            proc.stdout.close();reader.join(timeout=1)

def run_demo(output):
    modes=[('pass-through',False,False),('cache-only',True,False),
           ('coalescing-only',False,True),('cache-and-coalescing',True,True)]
    results=[run_mode(*mode) for mode in modes]
    assert len({row['outcomes_sha256'] for row in results})==1
    report={'experiment':'read-only-edge-http-ablation','environment':'Real loopback HTTP origin plus a separate edge process per configuration; one host, not WAN decentralization.',
        'workload':'38 synthetic reads: 8 simultaneous cold pinned reads, 8 warm repeats, 8 alternate-block repeats, 8 changing latest reads, 4 noncanonical-required errors and 2 missing-block errors.',
        'accounting':'Origin counts include two identity-bootstrap calls per configuration. JSON byte counts exclude HTTP/TLS overhead. No host hardware, latency benchmark or monetary savings is inferred.',
        'attribution':'Cache/coalescing primitives only. The biological graph is not involved in this experiment.',
        'public_network_used':False,'blockchain_transactions_sent':0,
        'all_modes_return_identical_outcomes':True,'results':results,
        'implementation_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['synafly_lab/rpc.py','synafly_lab/edge.py','synafly_lab/edge_server.py','scripts/demo_edge.py','tests/rpc_fixture.py']}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'experiment':report['experiment'],'same_outcomes':True,
        'origin_rpc_calls_including_bootstrap':{row['mode']:row['measured']['origin']['rpc_calls'] for row in results}},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='results/edge-http.json');a=p.parse_args();run_demo(a.out)
