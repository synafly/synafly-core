#!/usr/bin/env python3
"""LOCAL Anvil proof only. This command cannot target a public RPC or use user wallets."""
import argparse,hashlib,json,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.anchor import ReadOnlyRPC,commitment,tuple_data,commit_calldata,word
from synafly_lab.canonical import parse
from synafly_lab.checkpoint import Run
from synafly_lab.model import stimulus
ROOT=Path(__file__).resolve().parents[1]

def run_demo(output=None):
    subprocess.run(['forge','build','--offline'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    artifact=json.loads((ROOT/'out/ContinuityRegistry.sol/ContinuityRegistry.json').read_text())
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    # Never print Anvil's development mnemonic/keys. Only its unlocked local RPC is used.
    proc=subprocess.Popen(['anvil','--host','127.0.0.1','--port',str(port),'--chain-id','97','--accounts','3'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url='http://127.0.0.1:'+str(port)
    def rpc(method,params):
        # Private helper scoped exclusively to the newly spawned local process.
        r=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(r,timeout=5) as response:d=json.load(response)
        if 'error' in d:raise RuntimeError('Local EVM rejected '+method+': '+str(d['error'].get('message','')))
        return d['result']
    def wait_receipt(tx):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            receipt=rpc('eth_getTransactionReceipt',[tx])
            if receipt is not None:return receipt
            time.sleep(.02)
        raise RuntimeError('Local transaction receipt timeout')
    try:
        end=time.monotonic()+10
        while time.monotonic()<end:
            if proc.poll() is not None:raise RuntimeError('Local EVM exited')
            try:accounts=rpc('eth_accounts',[]);break
            except OSError:time.sleep(.05)
        else:raise RuntimeError('Local EVM readiness timeout')
        committee=sorted(accounts,key=lambda a:int(a,16));constructor=word(64)+word(2)+word(3)+''.join(a[2:].lower().rjust(64,'0') for a in committee)
        tx=rpc('eth_sendTransaction',[{'from':accounts[0],'data':artifact['bytecode']['object']+constructor,'gas':'0x4c4b40'}]);receipt=wait_receipt(tx);assert receipt['status']=='0x1';contract=receipt['contractAddress']
        run=Run(parse((ROOT/'data/malecns-sample.json').read_bytes()));genesis=run.genesis();next_cp=run.advance(genesis,[stimulus(run.spec['seed'],i,run.circuit.n) for i in range(8)])
        adapter=ReadOnlyRPC(url,97);proofs=[]
        for p in [genesis,next_cp]:
            c=commitment(p);prepared=adapter.prepare(contract,c)
            signatures=[rpc('eth_sign',[a,prepared['personal_sign_message']]) for a in committee[:2]]
            data=commit_calldata(c,signatures)
            tx=rpc('eth_sendTransaction',[{'from':accounts[2],'to':contract,'data':data,'gas':'0x7a120'}])
            local_receipt=wait_receipt(tx)
            proof=adapter.verify(tx,contract,c,confirmations=1);proof['gas_used']=int(local_receipt['gasUsed'],16);proofs.append(proof)
        report={'experiment':'local-evm-checkpoint-anchor','network':'LOCAL ANVIL ONLY','chain_id':97,'public_bsc_deployment':False,'note':'Using chain ID 97 locally is not a BSC testnet transaction. No real wallet or currency used.','registry_address':contract,'committee_size':3,'threshold':2,'genesis_and_successor_verified':True,'proofs':proofs,'runtime_code_sha256':hashlib.sha256(bytes.fromhex(rpc('eth_getCode',[contract,'latest'])[2:])).hexdigest()}
        if output:Path(output).write_text(json.dumps(report,indent=2)+'\n')
        return report
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='results/local-evm.json');a=p.parse_args();print(json.dumps(run_demo(a.out),indent=2))
