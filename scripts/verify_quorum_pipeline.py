#!/usr/bin/env python3
"""Actual owned-Anvil quorum commits; default mode audits recorded evidence offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.anchor import commit_calldata,word,EVENT_TOPIC
from synafly_lab.keccak import keccak256
from synafly_lab.offload_receipts import digest
from synafly_lab.quorum_local_evm import OwnedAnvil
from synafly_lab.quorum_relayer import (build_checkpoint,validate_bundle,aggregate_signatures,
    message_hash,signing_digest,verify_inclusion,abi_commitment)
from synafly_lab.quorum_rpc import RegistryRPCError,preflight,verify_transaction,head,selector
from synafly_lab.quorum_crypto import N
from synafly_lab.rpc import encode
from run_mesh_cluster import MeshCluster,request
ROOT=Path(__file__).resolve().parents[1]


def runtime_matches(artifact,code,threshold):
    expected=bytearray.fromhex(artifact['deployedBytecode']['object'][2:])
    references=artifact['deployedBytecode']['immutableReferences']
    if not references:raise ValueError('Expected immutable threshold references')
    for positions in references.values():
        for position in positions:
            if position['length']!=32:raise ValueError('Immutable width')
            expected[position['start']:position['start']+32]=threshold.to_bytes(32,'big')
    if bytes(expected)!=bytes.fromhex(code[2:]):raise ValueError('Deployed runtime differs from compiled registry')
    return '0x'+keccak256(bytes(expected)).hex()


def capture(node,action):
    node.wire=[];node.record=True
    try:return action(),list(node.wire)
    finally:node.record=False


def envelope(node,bundle,address,previous=None):
    validate_bundle(bundle,previous)
    signature=node.call('eth_sign',[address,bundle['message_hash']])
    return {'signer':address,'signature':signature,'bundle_hash':digest(bundle)}


def expect_revert(node,registry,c,signatures,error_name):
    # ABI encoder is the same one used for actual submission. Permit the single-
    # signature negative test by generating the normal layout explicitly.
    if len(signatures)==1:
        tuple_hex=''.join(c[k][2:] for k in ['lineage','graph','model','checkpoint','parent'])+word(c['sequence'])+word(c['tick'])
        data='0xde1046e7'+tuple_hex+word(256)+word(1)+word(32)+word(65)+signatures[0][2:].ljust(192,'0')
    else:data=commit_calldata(abi_commitment(c),signatures)
    before=head(node,registry,c['lineage'])
    try:node.call('eth_call',[{'to':registry,'data':data},'latest'])
    except RegistryRPCError as exc:
        expected='0x'+selector(error_name+'()')
        if exc.data!=expected:raise ValueError('Wrong EVM revert boundary: '+error_name)
    else:raise ValueError('Negative case unexpectedly succeeded')
    after=head(node,registry,c['lineage'])
    if after!=before:raise ValueError('Head changed across rejected simulation')
    return {'expected_error':error_name,'revert_data':expected,'mode':'actual-local-EVM-eth_call',
            'head_before':before,'head_after':after,'unchanged':True}


def live_local():
    subprocess.run(['forge','build','--offline'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    artifact=json.loads((ROOT/'out/ContinuityRegistry.sol/ContinuityRegistry.json').read_text())
    with tempfile.TemporaryDirectory() as temporary, OwnedAnvil() as node:
        directory=Path(temporary);committee=sorted(node.accounts[:3],key=lambda a:int(a,16));threshold=2
        constructor=word(64)+word(threshold)+word(3)+''.join(a[2:].rjust(64,'0') for a in committee)
        deploy_data=artifact['bytecode']['object']+constructor
        deployment_hash=node.call('eth_sendTransaction',[{'from':node.accounts[3],'data':deploy_data,'gas':'0x4c4b40'}])
        deployment=node.receipt(deployment_hash)
        if deployment['status']!='0x1':raise ValueError('Registry deployment failed')
        registry=deployment['contractAddress']
        code=node.call('eth_getCode',[registry,'latest']);runtime_hash=runtime_matches(artifact,code,threshold)
        target='0x0000000000000000000000000000000000000f1a'
        target_code='0x60085460005260206000f3'
        node.call('anvil_setCode',[target,target_code])
        node.call('anvil_setStorageAt',[target,'0x8','0x'+word(123)])
        node.call('anvil_setStorageAt',[target,'0x9','0x'+word(456)])
        node.call('evm_mine',[])
        pin={'blockHash':node.call('eth_getBlockByNumber',['latest',False])['hash'],'requireCanonical':False}
        catalog={'schema':'synafly.daemon-recipes.v1','chain_id':97,'genesis_hash':node.genesis,'entries':[
            {'code_hash':'0x'+keccak256(bytes.fromhex(target_code[2:])).hex(),'selector':'0x0902f1ac','data':'0x0902f1ac',
             'caller':'0x'+'00'*20,'value':'0x0','recipe':{'accesses':[['sload',['const',8]]],'guards':[]}}]}
        catalog_path=directory/'catalog.json';catalog_path.write_bytes(encode(catalog))
        with MeshCluster(node.url,97,node.genesis,directory/'cluster',catalog=catalog_path) as cluster:
            cluster_report=cluster.demonstrate(target,pin)
            node.call('evm_mine',[])
            warm_pin={'blockHash':node.call('eth_getBlockByNumber',['latest',False])['hash'],'requireCanonical':False}
            call_result=request(cluster.ports[0],'eth_call',[{'to':target,'data':'0x0902f1ac'},warm_pin])
            deadline=time.monotonic()+5
            while True:
                metrics=request(cluster.ports[0],path='/metrics')
                if metrics['prefetch'].get('slot_reads_completed',0)>=1 and not metrics['prefetch']['pending']:break
                if time.monotonic()>deadline:raise ValueError('Prefetch did not complete')
                time.sleep(.01)
            before=metrics['upstream']['rpc_calls']
            warmed=request(cluster.ports[0],'eth_getStorageAt',[target,'0x8',warm_pin])
            metrics=request(cluster.ports[0],path='/metrics')
            if call_result!=warmed or metrics['upstream']['rpc_calls']!=before:raise ValueError('Prefetch reuse not demonstrated')
            snapshot=request(cluster.ports[0],path='/receipts')
            cluster_report['receipts']=[request(port,path='/receipts') for port in cluster.ports]
            cluster_report['metrics']=[request(port,path='/metrics') for port in cluster.ports]
            cluster_report['prefetch_followup_hit']=True
        rows=snapshot['retained']
        if len(rows)<4:raise ValueError('Insufficient live receipts')
        split=len(rows)//2;previous=None;checkpoints=[];negatives={}
        for part in [rows[:split],rows[split:]]:
            bundle=build_checkpoint(part,chain_id=97,registry=registry,committee=committee,threshold=threshold,
                                    runtime_code_hash=runtime_hash,previous=previous)
            preflight_result,preflight_wire=capture(node,lambda:preflight(node,bundle,previous))
            # Each configured account approves a deterministic rebuild. These are
            # three distinct development signers, not three independent operators.
            signatures=[envelope(node,bundle,a,previous) for a in committee]
            envelopes=[signatures[2],signatures[0]]
            certificate=aggregate_signatures(bundle,envelopes,previous)
            if previous is None:
                s=certificate['signatures'];c=bundle['commitment']
                negatives['insufficient_quorum']=expect_revert(node,registry,c,s[:1],'InsufficientQuorum')
                negatives['duplicate_signer']=expect_revert(node,registry,c,[s[0],s[0]],'SignersNotOrdered')
                negatives['unsorted_signers']=expect_revert(node,registry,c,list(reversed(s)),'SignersNotOrdered')
                high=bytearray.fromhex(s[0][2:]);high[32:64]=(N-int.from_bytes(high[32:64],'big')).to_bytes(32,'big');high[64]=55-high[64]
                negatives['high_s']=expect_revert(node,registry,c,['0x'+high.hex(),s[1]],'InvalidSignature')
                for key,chain,contract in [('cross_chain',98,registry),('cross_contract',97,target)]:
                    wrong_message=message_hash(c,chain,contract)
                    wrong=[node.call('eth_sign',[a,wrong_message]) for a in certificate['witnesses']]
                    negatives[key]=expect_revert(node,registry,c,wrong,'UnknownWitness')
            else:
                changed={**bundle['commitment'],'parent':'0x'+'ff'*32}
                negatives['wrong_parent']=expect_revert(node,registry,changed,certificate['signatures'],'WrongParentOrSequence')
                changed={**bundle['commitment'],'graph':'0x'+'ff'*32}
                negatives['changed_graph']=expect_revert(node,registry,changed,certificate['signatures'],'ModelChanged')
            tx={**certificate['transaction'],'from':node.accounts[3],'gas':'0x7a120'}
            tx_hash=node.call('eth_sendTransaction',[tx]);receipt=node.receipt(tx_hash)
            if receipt['status']!='0x1':raise ValueError('Checkpoint transaction reverted')
            verified,verification_wire=capture(node,lambda:verify_transaction(node,bundle,certificate,tx_hash))
            negatives['replay_'+str(bundle['commitment']['sequence'])]=expect_revert(node,registry,bundle['commitment'],certificate['signatures'],'WrongParentOrSequence')
            checkpoints.append({'bundle':bundle,'witness_envelopes':signatures,'certificate':certificate,
                                'preflight':preflight_result,'preflight_wire':preflight_wire,
                                'verification':verified,'verification_wire':verification_wire})
            previous=bundle
        # One actual reverted local transaction: never reinterpret a failure as a commit.
        rejected_tx={**checkpoints[0]['certificate']['transaction'],'from':node.accounts[3],'gas':'0x7a120'}
        head_before=head(node,registry,previous['commitment']['lineage'])
        rejected_hash=node.call('eth_sendTransaction',[rejected_tx]);rejected_receipt=node.receipt(rejected_hash)
        head_after=head(node,registry,previous['commitment']['lineage'])
        if rejected_receipt['status']!='0x0' or rejected_receipt['logs'] or head_after!=head_before:
            raise ValueError('Replay transaction was not rejected cleanly')
        rejected={'receipt':rejected_receipt,'transaction':node.call('eth_getTransactionByHash',[rejected_hash]),
                  'head_before':head_before,'head_after':head_after,'preceding_simulation_error':'WrongParentOrSequence'}
        return {'schema':'synafly.quorum-pipeline.v1','network':'LOCAL ANVIL ONLY','chain_id':97,
                'public_bsc_deployment':False,'public_transactions_sent':0,'registry_address':registry,
                'deployment_receipt':deployment,'deployment_transaction':node.call('eth_getTransactionByHash',[deployment_hash]),
                'registry_runtime':code,'runtime_code_hash':runtime_hash,
                'committee':committee,'threshold':threshold,'local_commit_transactions':len(checkpoints),
                'local_rejected_transactions':1,'rejected_replay_transaction':rejected,
                'source_cluster':cluster_report,'checkpointed_role':0,'checkpointed_receipts':len(rows),
                'checkpoints':checkpoints,'negative_evm_cases':negatives,'local_control_rpc_counts':dict(node.counts),
                'contract_source_sha256':hashlib.sha256((ROOT/'contracts/ContinuityRegistry.sol').read_bytes()).hexdigest(),
                'note':'Actual local EVM deployment/commit transactions. Local chain ID 97 is not BSC Testnet. Synthetic target state; no real private keys or funds. Quorum attests receipt integrity, not independent economic savings.'}


class WireReplay:
    """Strict ordered replay of captured real-EVM reads, not an EVM simulator."""
    def __init__(self,rows):self.rows=list(rows);self.index=0
    def call(self,method,params):
        if self.index>=len(self.rows):raise ValueError('Unexpected extra wire request')
        row=self.rows[self.index];self.index+=1
        if method!=row['method'] or encode(params)!=encode(row['params']):raise ValueError('Wire request mismatch')
        response=row['response']
        if 'error' in response:raise RegistryRPCError(response['error']['code'],response['error'].get('data'))
        return response['result']
    def complete(self):
        if self.index!=len(self.rows):raise ValueError('Unused wire evidence')


def audit(report):
    if report['contract_source_sha256']!=hashlib.sha256((ROOT/'contracts/ContinuityRegistry.sol').read_bytes()).hexdigest():raise ValueError('Registry source binding')
    if report['network']!='LOCAL ANVIL ONLY' or report['public_bsc_deployment'] is not False or report['public_transactions_sent']!=0:raise ValueError('Evidence scope')
    if '0x'+keccak256(bytes.fromhex(report['registry_runtime'][2:])).hex()!=report['runtime_code_hash']:raise ValueError('Runtime hash')
    previous=None;receipts=0;anchored=[]
    for row in report['checkpoints']:
        bundle=row['bundle'];validate_bundle(bundle,previous)
        for receipt,proof in zip(bundle['receipts'],bundle['proofs']):verify_inclusion(receipt,proof,bundle['merkle_root'])
        selected=[e for e in row['witness_envelopes'] if e['signer'] in row['certificate']['witnesses']]
        if aggregate_signatures(bundle,list(reversed(selected)),previous)!=row['certificate']:raise ValueError('Certificate mismatch')
        wire=WireReplay(row['preflight_wire']);actual=preflight(wire,bundle,previous);wire.complete()
        if actual!=row['preflight']:raise ValueError('Preflight evidence mismatch')
        wire=WireReplay(row['verification_wire']);actual=verify_transaction(wire,bundle,row['certificate'],row['verification']['receipt']['transactionHash']);wire.complete()
        if actual!=row['verification']:raise ValueError('Transaction evidence mismatch')
        previous=bundle;receipts+=len(bundle['receipts']);anchored.extend(bundle['receipts'])
    if receipts!=report['checkpointed_receipts'] or len(report['checkpoints'])!=report['local_commit_transactions']:raise ValueError('Receipt/commit accounting')
    if anchored!=report['source_cluster']['receipts'][report['checkpointed_role']]['retained']:raise ValueError('Checkpoint receipts differ from daemon capture')
    rejected=report['rejected_replay_transaction']
    if report['local_rejected_transactions']!=1 or rejected['receipt']['status']!='0x0' or rejected['receipt']['logs'] or rejected['head_before']!=rejected['head_after']:
        raise ValueError('Rejected replay transaction evidence')
    if rejected['receipt']['transactionHash']!=rejected['transaction']['hash'] or rejected['transaction']['to']!=report['registry_address'] or int(rejected['receipt']['gasUsed'],16)<=0:raise ValueError('Rejected transaction identity/gas')
    if rejected['transaction']['input']!=report['checkpoints'][0]['certificate']['transaction']['data']:
        raise ValueError('Replay transaction input mismatch')
    for row in report['negative_evm_cases'].values():
        if row['revert_data']!='0x'+selector(row['expected_error']+'()') or row['head_before']!=row['head_after']:raise ValueError('Negative EVM evidence')
    return {'schema':'synafly.quorum-audit.v1','mode':'offline-recorded-wire','checkpoints':len(report['checkpoints']),
            'receipts':receipts,'cryptographic_recovery':'verified','merkle_proofs':'verified',
            'rpc_replay':'matched','real_evm_rerun':False}


def verify_release_lock():
    lock=json.loads((ROOT/'results/quorum-pipeline-release-lock.json').read_text())
    if lock['schema']!='synafly.quorum-release-lock.v1':raise ValueError('Release lock schema')
    for name,expected in lock['sha256'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Release lock path')
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=expected:raise ValueError('Quorum source/evidence binding: '+name)
    return {'source_bindings':'matched','files':len(lock['sha256'])}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local-anvil',action='store_true')
    p.add_argument('--report',default='results/quorum-pipeline.json');p.add_argument('--out',default='.local/quorum-verification.json');args=p.parse_args()
    if not args.local_anvil:verify_release_lock()
    value=live_local() if args.local_anvil else audit(json.loads(Path(args.report).read_text()))
    if args.local_anvil:audit(value)
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(value)+b'\n')
    print(json.dumps({'mode':'actual-local-anvil' if args.local_anvil else 'offline-audit','status':'passed','public_broadcast':False}))
if __name__=='__main__':main()
