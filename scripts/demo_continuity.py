#!/usr/bin/env python3
"""Three real keeper processes. Failure and fresh-node recovery, no public network writes."""
import argparse,json,os,secrets,socket,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.canonical import digest,parse
from synafly_lab.checkpoint import Run
from synafly_lab.model import stimulus

ROOT=Path(__file__).resolve().parents[1]
def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def request(url,path,body=None,token=None):
    headers={}
    if body is not None:headers['Content-Type']='application/json'
    if token:headers['Authorization']='Bearer '+token
    r=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
    with urllib.request.urlopen(r,timeout=10) as response:return json.load(response)
def wait_ready(url,process):
    end=time.monotonic()+10
    while time.monotonic()<end:
        if process.poll() is not None:raise RuntimeError('Keeper exited before readiness')
        try:return request(url,'/health')
        except (OSError,ValueError):time.sleep(.05)
    raise RuntimeError('Keeper readiness timeout')

def run_demo(output=None):
    processes=[]
    with tempfile.TemporaryDirectory(prefix='synafly-continuity-') as tmp:
        ports=[free_port() for _ in range(3)]
        while len(set(ports))!=3:ports=[free_port() for _ in range(3)]
        urls=['http://127.0.0.1:'+str(p) for p in ports]
        tokens=[secrets.token_hex(24) for _ in ports]
        graph=parse((ROOT/'data/malecns-sample.json').read_bytes());run=Run(graph)
        def start(i,peer,graph_path=None):
            args=[sys.executable,'-m','synafly_lab','--node-id','keeper-'+str(i),'--store',str(Path(tmp)/str(i)/'state.sqlite'),'--graph',str(graph_path or ROOT/'data/malecns-sample.json'),'--port',str(ports[i]),'--peer','survivor='+peer]
            proc=subprocess.Popen(args,cwd=ROOT,env={**os.environ,'LAB_ADMIN_TOKEN':tokens[i]},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            processes.append(proc);wait_ready(urls[i],proc);return proc
        try:
            original=start(0,urls[1]);start(1,urls[0])
            chain=[run.genesis()]
            for checkpoint_index in range(8):
                head=request(urls[0],'/v1/head');frames=[stimulus(run.spec['seed'],head['tick']+i,run.circuit.n) for i in range(8)]
                made=request(urls[0],'/v1/advance',{'expected_parent':head['hash'],'inputs':frames},tokens[0]);chain.append(made['checkpoint'])
            replicated=request(urls[1],'/v1/sync',{'peer':'survivor'},tokens[1]);assert replicated['hash']==digest(chain[-1])
            before=request(urls[1],'/v1/head')
            original.terminate();original.wait(timeout=5)
            # C starts for the first time AFTER A is gone, and knows only B's URL.
            recovered_spec=request(urls[1],'/v1/spec');assert digest(recovered_spec)==run.id
            recovered_graph=Path(tmp)/'recovered-graph.json';recovered_graph.write_text(json.dumps(recovered_spec['graph']))
            start(2,urls[1],recovered_graph);recovered=request(urls[2],'/v1/sync',{'peer':'survivor'},tokens[2])
            assert recovered['hash']==before['hash']
            h=request(urls[2],'/v1/head');frames=[stimulus(run.spec['seed'],h['tick']+i,run.circuit.n) for i in range(8)]
            resumed=request(urls[2],'/v1/advance',{'expected_parent':h['hash'],'inputs':frames},tokens[2]);expected=run.advance(chain[-1],frames)
            assert resumed['hash']==digest(expected) and resumed['checkpoint']==expected
            report={'experiment':'three-process-checkpoint-recovery','environment':'single-host loopback, independent processes and SQLite files; not geographically decentralized','nodes':3,'graph_nodes':run.circuit.n,'graph_edges':len(graph['edges']),'run_id':run.id,'original_terminated_before_new_node_start':True,'graph_restored_from_surviving_peer':True,'replicated_checkpoints':replicated['imported'],'recovered_checkpoint':recovered['hash'],'resumed_tick':expected['tick'],'resumed_state_hash':expected['state_hash'],'matches_uninterrupted_execution':True,'tokens_or_private_keys_in_report':False,'public_bsc_transaction':False}
            if output:
                output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
                (output.parent/'checkpoints.json').write_text(json.dumps({'spec':run.spec,'checkpoints':chain+[expected]},separators=(',',':'))+'\n')
            return report
        finally:
            for proc in processes:
                if proc.poll() is None:proc.terminate()
            for proc in processes:
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='results/continuity.json');a=p.parse_args();print(json.dumps(run_demo(a.out),indent=2))
