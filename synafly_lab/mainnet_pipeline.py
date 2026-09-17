"""Guarded mainnet broadcast of exact signed bytes; no token or key-custody logic."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import time
import ipaddress
from urllib.parse import urlsplit
from .mainnet_codec import decode_transaction
from .mainnet_plan import IDENTITY,TOKEN_CA,validate_plan
from .offload_receipts import digest
from .quorum_relayer import aggregate_signatures
from .quorum_rpc import ReadRPC,preflight,verify_transaction
from .rpc import BSC_GENESIS,encode,quantity,hex_data


class MainnetRPC(ReadRPC):
    def call(self,method,params):
        if method in {'eth_getTransactionCount','eth_getBalance','eth_gasPrice','eth_estimateGas','eth_sendRawTransaction'}:
            return self._exchange(method,params)
        return super().call(method,params)


def fee_wei(value):
    if type(value) is not str or not re.fullmatch(r'\d+(?:\.\d{1,18})?',value):raise ValueError('BNB cap must be a plain decimal with at most 18 places')
    whole,_,fraction=value.partition('.');result=int(whole)*10**18+int(fraction.ljust(18,'0') or '0')
    if not 0<result<10**20:raise ValueError('Fee cap outside bound')
    return result


def network_check(rpc):
    if quantity(rpc.call('eth_chainId',[]))!='0x38':raise ValueError('Refusing wrong chain')
    genesis=rpc.call('eth_getBlockByNumber',['0x0',False])
    if not genesis or genesis.get('hash')!=BSC_GENESIS or genesis.get('number')!='0x0':raise ValueError('Refusing wrong genesis/network')


def check_certificate(plan,certificate):
    b=plan['genesis_bundle']
    envelopes=[{'signer':a,'signature':s,'bundle_hash':digest(b)} for a,s in zip(certificate['witnesses'],certificate['signatures'])]
    checked=aggregate_signatures(b,envelopes)
    if checked!=certificate:raise ValueError('Certificate mismatch')


def check_pair(plan,certificate,raw_deploy,raw_commit,max_fee):
    check_certificate(plan,certificate)
    first,second=decode_transaction(raw_deploy),decode_transaction(raw_commit)
    for tx,nonce,limit in [(first,plan['nonce'],2000000),(second,plan['nonce']+1,500000)]:
        if tx['chain_id']!=56 or tx['sender']!=plan['deployer'] or tx['nonce']!=nonce or tx['value']!=0:raise ValueError('Signed transaction chain/sender/nonce/value mismatch')
        if not 21000<=tx['gas_limit']<=limit or tx['max_fee_per_gas']<=0:raise ValueError('Gas policy')
    if first['to'] is not None or first['data']!=plan['creation_data']:raise ValueError('Deployment transaction differs from exact reviewed initcode')
    if second['to']!=plan['registry_address'] or second['to']==TOKEN_CA or second['data']!=certificate['transaction']['data']:raise ValueError('Genesis destination/calldata mismatch')
    maximum=sum(t['gas_limit']*t['max_fee_per_gas'] for t in (first,second))
    if type(max_fee) is not int or maximum>max_fee:raise ValueError('Maximum signed fees exceed approved total BNB cap')
    return first,second,maximum


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise ValueError('Refusing symlink output')
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:f.write(encode(value)+b'\n');f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if temporary.exists():temporary.unlink()


@contextmanager
def journal_lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(str(path)+'.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another deployment process holds this journal') from None
        yield
    finally:os.close(fd)


def wait_confirmed(rpc,tx_hash,*,timeout=300,poll=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        receipt=rpc.call('eth_getTransactionReceipt',[tx_hash])
        if receipt is not None:
            if receipt.get('status')!='0x1':raise ValueError('Transaction failed; retain journal and do not retry blindly')
            height=int(quantity(receipt['blockNumber']),16)
            block=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
            if not block or block.get('hash')!=receipt.get('blockHash'):raise ValueError('Receipt reorg/mismatch; stop and reconcile')
            if int(quantity(rpc.call('eth_blockNumber',[])),16)-height+1>=3:return receipt
        time.sleep(poll)
    raise TimeoutError('Confirmation pending/unknown; resume this journal without resending')


def deployment_receipt_check(rpc,plan,tx,receipt):
    if receipt.get('transactionHash')!=tx['hash'] or receipt.get('contractAddress')!=plan['registry_address'] or receipt.get('from')!=plan['deployer'] or receipt.get('to') is not None:raise ValueError('Deployment receipt identity')
    transaction=rpc.call('eth_getTransactionByHash',[tx['hash']])
    if not transaction or transaction.get('from')!=plan['deployer'] or transaction.get('to') is not None or transaction.get('input')!=plan['creation_data'] or transaction.get('blockHash')!=receipt['blockHash'] or int(quantity(transaction['value']),16)!=0:raise ValueError('Deployment transaction mismatch')
    if int(quantity(transaction['nonce']),16)!=tx['nonce'] or int(quantity(transaction['gas']),16)!=tx['gas_limit']:raise ValueError('Deployment nonce/gas mismatch')
    if int(quantity(receipt['effectiveGasPrice']),16)>tx['max_fee_per_gas']:raise ValueError('Deployment effective fee exceeds signed cap')
    if not 0<int(quantity(receipt['gasUsed']),16)<=tx['gas_limit']:raise ValueError('Deployment gas receipt')
    code=rpc.call('eth_getCode',[plan['registry_address'],{'blockHash':receipt['blockHash'],'requireCanonical':True}])
    from .keccak import keccak256
    if '0x'+keccak256(bytes.fromhex(hex_data(code)[2:])).hex()!=plan['runtime_code_hash']:raise ValueError('Deployed code mismatch')


def run_stages(rpc,plan,certificate,first,second,max_fee,journal,output,*,timeout=300,poll=3,journal_hints=None):
    """Internal state machine; caller must validate plan, signature pair and operator scope."""
    journal=Path(journal);output=Path(output);plan_hash=digest(plan)
    expected={**IDENTITY,'schema':'synafly.mainnet-journal.v1','plan_hash':plan_hash,'deploy_hash':first['hash'],'commit_hash':second['hash'],'max_fee_wei':max_fee}
    with journal_lock(journal):
        state=json.loads(journal.read_text()) if journal.exists() else {**(journal_hints or {}),**expected,'deploy_state':'new','commit_state':'new'}
        if any(state.get(k)!=v for k,v in expected.items()):raise ValueError('Journal differs from approved plan or signed hashes')
        old={}
        if output.exists():
            old=json.loads(output.read_text())
            if old.get('status') not in {'not_deployed','prepared'} and old.get('plan_hash')!=plan_hash:raise ValueError('Refusing to overwrite another deployment record')
        network_check(rpc)
        for stage,tx in [('deploy',first),('commit',second)]:
            if stage=='commit':
                deployment=wait_confirmed(rpc,first['hash'],timeout=timeout,poll=poll)
                deployment_receipt_check(rpc,plan,first,deployment)
            if state[stage+'_state']=='new':
                if int(quantity(rpc.call('eth_getTransactionCount',[plan['deployer'],'pending'])),16)!=tx['nonce']:raise ValueError('Pending nonce changed; stop before signing/replacement')
                needed=tx['gas_limit']*tx['max_fee_per_gas']+(second['gas_limit']*second['max_fee_per_gas'] if stage=='deploy' else 0)
                if int(quantity(rpc.call('eth_getBalance',[plan['deployer'],'pending'])),16)<needed:raise ValueError('Insufficient balance for maximum approved fees')
                if stage=='deploy':
                    if rpc.call('eth_getCode',[plan['registry_address'],'latest'])!='0x':raise ValueError('Predicted registry address already has code')
                else:
                    preflight(rpc,plan['genesis_bundle'])
                    rpc.call('eth_call',[{'from':plan['deployer'],'to':plan['registry_address'],'data':certificate['transaction']['data']},'latest'])
                state[stage+'_state']='sending';atomic_json(journal,state)
                # Hash is durable BEFORE the write. A timeout/error never triggers a resend.
                returned=rpc.call('eth_sendRawTransaction',[tx['raw']])
                if returned!=tx['hash']:raise ValueError('Provider returned unexpected transaction hash; reconcile journal')
            receipt=wait_confirmed(rpc,tx['hash'],timeout=timeout,poll=poll)
            state[stage+'_state']='confirmed';state[stage+'_receipt']=receipt;atomic_json(journal,state)
            if stage=='deploy':
                deployment_receipt_check(rpc,plan,tx,receipt)
                if old.get('status')!='deployed':atomic_json(output,{**IDENTITY,'network':'bsc-mainnet','chain_id':56,'status':'deployed_pending_genesis','plan_hash':plan_hash,'registry_address':plan['registry_address'],'deploy_tx_hash':first['hash'],'genesis_commit_tx_hash':None,'committee':plan['committee'],'threshold':2})
        proof=verify_transaction(rpc,plan['genesis_bundle'],certificate,second['hash'],confirmations=3)
        if proof['transaction']['from']!=plan['deployer'] or int(quantity(proof['transaction']['nonce']),16)!=second['nonce'] or int(quantity(proof['transaction']['gas']),16)!=second['gas_limit']:raise ValueError('Genesis sender/nonce/gas mismatch')
        if int(quantity(proof['receipt']['effectiveGasPrice']),16)>second['max_fee_per_gas']:raise ValueError('Genesis fee exceeds signed cap')
        spent=sum(int(quantity(state[k+'_receipt']['gasUsed']),16)*int(quantity(state[k+'_receipt']['effectiveGasPrice']),16) for k in ('deploy','commit'))
        if spent>max_fee:raise ValueError('Actual fees exceed approved cap')
        deployed={**IDENTITY,'network':'bsc-mainnet','chain_id':56,'status':'deployed','plan_hash':plan_hash,
                  'registry_address':plan['registry_address'],'deploy_tx_hash':first['hash'],'genesis_commit_tx_hash':second['hash'],
                  'block_number':int(quantity(proof['receipt']['blockNumber']),16),'committee':plan['committee'],'threshold':2,
                  'lineage':plan['genesis_bundle']['commitment']['lineage'],'merkle_root':plan['genesis_bundle']['merkle_root'],
                  'runtime_code_hash':plan['runtime_code_hash'],'bscscan_verification_url':'https://bscscan.com/address/'+plan['registry_address']+'#code',
                  'bscscan_verification_status':'not_requested','total_fee_paid_wei':spent,'max_total_fee_wei':max_fee,'confirmations_at_check':proof['confirmations'],
                  'deploy_receipt':state['deploy_receipt'],'genesis_receipt':proof['receipt'],'genesis_scope':plan['genesis_scope']}
        atomic_json(output,deployed);return deployed


def broadcast(rpc,plan,certificate,raw_deploy,raw_commit,max_fee,journal,output,artifact,root,**wait_options):
    validate_plan(plan,artifact,root)
    endpoint=urlsplit(rpc.url)
    if endpoint.scheme!='https' or endpoint.hostname in {'localhost','127.0.0.1','::1'}:raise ValueError('Mainnet broadcast requires a public HTTPS RPC, not a local fork')
    try:address=ipaddress.ip_address(endpoint.hostname)
    except ValueError:address=None
    if address is not None and not address.is_global:raise ValueError('Refusing private-address broadcast endpoint')
    fixture=json.loads((Path(root)/'tests/fixtures/genesis_checkpoint.json').read_text())
    if plan['kind']!='operator-plan' or plan['deployer']==fixture['plan']['deployer'] or any(a in fixture['plan']['committee'] for a in plan['committee']):raise ValueError('Public codec fixture accounts must NEVER be used for mainnet broadcast')
    first,second,_=check_pair(plan,certificate,raw_deploy,raw_commit,max_fee)
    return run_stages(rpc,plan,certificate,first,second,max_fee,journal,output,**wait_options)
