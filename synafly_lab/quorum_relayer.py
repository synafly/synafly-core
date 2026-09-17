"""Receipt Merkle checkpoints and EIP-191 quorum certificates; no key custody."""
import hashlib
from .anchor import commit_calldata, tuple_data
from .keccak import keccak256
from .offload_receipts import digest as receipt_digest, ZERO
from .quorum_crypto import recover_address
from .rpc import encode, hex_data, quantity

BATCH_SCHEMA = 'synafly.quorum-checkpoint.v1'
DOMAIN = keccak256(b'SynaFly.ContinuityRegistry.v1')
SCOPE = 'receipt-integrity-and-continuity-only; not independent proof of origin savings'
FIELDS = {'schema','timestamp','block_hash','target_contract','target_slot','upstream_saved',
          'source','lineage','sequence','parent','receipt_hash','commitment'}
HASH_FIELDS = ('lineage','graph','model','checkpoint','parent')


def integer(value, low=0, high=2**64-1):
    if type(value) is not int or not low <= value <= high: raise ValueError('Integer bound')
    return value


def strict_hex(value, size):
    normalized = hex_data(value, size)
    if normalized != value: raise ValueError('Lowercase canonical hex required')
    return value


def validate_commitment(c):
    if type(c) is not dict or set(c) != {*HASH_FIELDS,'sequence','tick'}: raise ValueError('Commitment fields')
    for field in HASH_FIELDS:
        strict_hex(c[field],32)
        if field != 'parent' and c[field] == ZERO: raise ValueError('Zero commitment identity')
    integer(c['sequence']); integer(c['tick'])
    return c


def abi_commitment(c):
    validate_commitment(c)
    return {k:v[2:] if k in HASH_FIELDS else v for k,v in c.items()}


def validate_receipts(rows, previous=None):
    if type(rows) is not list or not 1 <= len(rows) <= 256: raise ValueError('Receipt batch bound')
    if len(encode(rows)) > 524288: raise ValueError('Receipt encoding bound')
    expected = 0 if previous is None else integer(previous['last_receipt_sequence']) + 1
    parent = ZERO if previous is None else strict_hex(previous['receipts'][-1]['receipt_hash'],32)
    first = rows[0]
    for row in rows:
        if type(row) is not dict or set(row) != FIELDS or row['schema'] != 'synafly.offload-receipt.v1':
            raise ValueError('Receipt schema')
        integer(row['timestamp']); integer(row['sequence'])
        if type(row['upstream_saved']) is not int or row['upstream_saved'] != 1: raise ValueError('Receipt accounting unit')
        if type(row['source']) is not str or row['source'] not in {'cache','coalesced','peer'}: raise ValueError('Receipt source')
        for key in ('block_hash','lineage','parent','receipt_hash'): strict_hex(row[key],32)
        strict_hex(row['target_contract'],20)
        if quantity(row['target_slot']) != row['target_slot']: raise ValueError('Slot encoding')
        if row['sequence'] != expected or row['parent'] != parent: raise ValueError('Receipt gap/replay/parent')
        core = {k:v for k,v in row.items() if k not in {'receipt_hash','commitment'}}
        if receipt_digest(core) != row['receipt_hash']: raise ValueError('Receipt hash mismatch')
        c = validate_commitment(row['commitment'])
        if any(c[k] != row[k] for k in ('lineage','parent','sequence')) or c['checkpoint'] != row['receipt_hash'] or c['tick'] != row['sequence']:
            raise ValueError('Embedded receipt commitment mismatch')
        if row['lineage'] != first['lineage'] or any(c[k] != first['commitment'][k] for k in ('graph','model')):
            raise ValueError('Mixed receipt streams/models')
        if previous is not None and (row['lineage'] != previous['receipt_lineage'] or c['graph'] != previous['receipt_graph'] or c['model'] != previous['receipt_model']):
            raise ValueError('Receipt stream changed')
        expected += 1; parent = row['receipt_hash']


def leaf(row): return hashlib.sha256(b'\x00'+encode(row)).digest()
def branch(left,right): return hashlib.sha256(b'\x01'+left+right).digest()
def seal_root(root,count): return '0x'+hashlib.sha256(b'\x02'+count.to_bytes(8,'big')+root).hexdigest()


def merkle(rows):
    if type(rows) is not list or not 1 <= len(rows) <= 256: raise ValueError('Merkle count')
    levels = [[leaf(row) for row in rows]]
    while len(levels[-1]) > 1:
        current = levels[-1]
        levels.append([branch(current[i],current[min(i+1,len(current)-1)]) for i in range(0,len(current),2)])
    proofs = []
    for i in range(len(rows)):
        index=i; siblings=[]
        for level in levels[:-1]:
            siblings.append('0x'+level[min(index^1,len(level)-1)].hex()); index//=2
        proofs.append({'index':i,'count':len(rows),'siblings':siblings})
    return seal_root(levels[-1][0],len(rows)), proofs


def verify_inclusion(row,proof,root):
    if type(proof) is not dict or set(proof) != {'index','count','siblings'}: raise ValueError('Proof fields')
    count=integer(proof['count'],1,256); index=integer(proof['index'],0,count-1)
    if type(proof['siblings']) is not list or len(proof['siblings']) != (count-1).bit_length(): raise ValueError('Proof depth')
    value=leaf(row); width=count
    for item in proof['siblings']:
        sibling=bytes.fromhex(strict_hex(item,32)[2:])
        if index % 2 == 0 and index+1 == width and sibling != value: raise ValueError('Odd duplication mismatch')
        value=branch(sibling,value) if index%2 else branch(value,sibling)
        index//=2; width=(width+1)//2
    if seal_root(value,count) != strict_hex(root,32): raise ValueError('Merkle proof mismatch')
    return True


def message_hash(c,chain_id,registry):
    integer(chain_id,1,2**63-1); strict_hex(registry,20)
    payload=DOMAIN+chain_id.to_bytes(32,'big')+bytes.fromhex(registry[2:]).rjust(32,b'\0')+bytes.fromhex(tuple_data(abi_commitment(c)))
    return '0x'+keccak256(payload).hex()


def signing_digest(message):
    return '0x'+keccak256(b'\x19Ethereum Signed Message:\n32'+bytes.fromhex(strict_hex(message,32)[2:])).hex()


def committee_policy(members,threshold):
    if type(members) is not list or not 2 <= len(members) <= 16: raise ValueError('Committee size')
    normalized=[strict_hex(a,20) for a in members]
    if normalized != sorted(set(normalized),key=lambda a:int(a,16)) or any(int(a,16)==0 for a in normalized):
        raise ValueError('Committee must be distinct and sorted')
    integer(threshold,2,len(members))


def model_hash(receipt_model,runtime_code_hash,committee,threshold):
    return receipt_digest(['synafly.offload-merkle-model.v1',receipt_model,SCOPE,runtime_code_hash,committee,threshold])


def validate_previous(previous):
    """Verify a supplied predecessor's contents; preflight also requires its on-chain head."""
    if type(previous) is not dict or previous.get('schema') != BATCH_SCHEMA: raise ValueError('Predecessor schema')
    c=validate_commitment(previous['commitment']); rows=previous['receipts']
    if type(rows) is not list or not rows:raise ValueError('Predecessor receipts')
    if c['sequence']==0:
        validate_receipts(rows)
        if c['tick']!=0 or c['parent']!=ZERO:raise ValueError('Predecessor genesis')
    else:
        anchor={'last_receipt_sequence':integer(rows[0]['sequence'],1)-1,
                'receipts':[{'receipt_hash':rows[0]['parent']}],
                **{key:previous[key] for key in ('receipt_lineage','receipt_graph','receipt_model')}}
        validate_receipts(rows,anchor)
        if c['sequence']>rows[0]['sequence'] or c['tick']!=rows[-1]['sequence']+1 or c['parent']==ZERO:raise ValueError('Predecessor sequence/tick')
    root,proofs=merkle(rows)
    if previous['merkle_root']!=root or c['checkpoint']!=root or previous['proofs']!=proofs:raise ValueError('Predecessor Merkle mismatch')
    if previous['first_receipt_sequence']!=rows[0]['sequence'] or previous['last_receipt_sequence']!=rows[-1]['sequence']:raise ValueError('Predecessor cursor')
    if previous['receipt_lineage']!=rows[0]['lineage'] or previous['receipt_graph']!=rows[0]['commitment']['graph'] or previous['receipt_model']!=rows[0]['commitment']['model']:raise ValueError('Predecessor stream')
    if c['graph']!=previous['receipt_graph'] or c['model']!=model_hash(previous['receipt_model'],previous['runtime_code_hash'],previous['committee'],previous['threshold']):raise ValueError('Predecessor model')
    if c['lineage']!=receipt_digest(['synafly.offload-merkle-lineage.v1',previous['chain_id'],previous['registry'],previous['receipt_lineage']]):raise ValueError('Predecessor lineage')
    committee_policy(previous['committee'],previous['threshold']);strict_hex(previous['runtime_code_hash'],32)
    message=message_hash(c,previous['chain_id'],previous['registry'])
    if previous['message_hash']!=message or previous['signing_digest']!=signing_digest(message) or previous['validation_scope']!=SCOPE:raise ValueError('Predecessor signing domain')


def build_checkpoint(rows,*,chain_id,registry,committee,threshold,runtime_code_hash,previous=None):
    integer(chain_id,1,2**63-1);strict_hex(registry,20);strict_hex(runtime_code_hash,32)
    committee_policy(committee,threshold)
    if previous is not None:validate_previous(previous)
    validate_receipts(rows,previous)
    first=rows[0]; root,proofs=merkle(rows)
    model=model_hash(first['commitment']['model'],runtime_code_hash,committee,threshold)
    lineage=receipt_digest(['synafly.offload-merkle-lineage.v1',chain_id,registry,first['lineage']])
    sequence=0 if previous is None else integer(previous['commitment']['sequence'])+1
    if previous is not None:
        for key,value in [('chain_id',chain_id),('registry',registry),('committee',committee),('threshold',threshold),('runtime_code_hash',runtime_code_hash)]:
            if previous[key]!=value:raise ValueError('Checkpoint domain or committee changed')
    c={'lineage':lineage,'graph':first['commitment']['graph'],'model':model,'checkpoint':root,
       'parent':ZERO if previous is None else previous['commitment']['checkpoint'],
       'sequence':sequence,'tick':0 if previous is None else rows[-1]['sequence']+1}
    validate_commitment(c)
    message=message_hash(c,chain_id,registry)
    return {'schema':BATCH_SCHEMA,'chain_id':chain_id,'registry':registry,'committee':committee,'threshold':threshold,
            'runtime_code_hash':runtime_code_hash,'receipt_lineage':first['lineage'],'receipt_graph':first['commitment']['graph'],
            'receipt_model':first['commitment']['model'],'first_receipt_sequence':first['sequence'],
            'last_receipt_sequence':rows[-1]['sequence'],'receipts':rows,'merkle_root':root,'proofs':proofs,
            'commitment':c,'message_hash':message,'signing_digest':signing_digest(message),'validation_scope':SCOPE}


def validate_bundle(bundle,previous=None):
    if type(bundle) is not dict or bundle.get('schema') != BATCH_SCHEMA: raise ValueError('Bundle schema')
    try:
        rebuilt=build_checkpoint(bundle['receipts'],chain_id=bundle['chain_id'],registry=bundle['registry'],
            committee=bundle['committee'],threshold=bundle['threshold'],runtime_code_hash=bundle['runtime_code_hash'],previous=previous)
        if encode(rebuilt)!=encode(bundle):raise ValueError('Bundle differs from deterministic rebuild')
    except (KeyError,TypeError) as exc:raise ValueError('Bundle fields') from exc
    return rebuilt


def aggregate_signatures(bundle,envelopes,previous=None):
    validate_bundle(bundle,previous)
    if type(envelopes) is not list or not bundle['threshold'] <= len(envelopes) <= len(bundle['committee']):
        raise ValueError('Insufficient quorum or excessive signatures')
    signed={}; bundle_hash=receipt_digest(bundle)
    for envelope in envelopes:
        if type(envelope) is not dict or set(envelope) != {'signer','signature','bundle_hash'} or envelope['bundle_hash']!=bundle_hash:
            raise ValueError('Signature envelope/bundle hash')
        recovered=recover_address(bundle['signing_digest'],envelope['signature'])
        if recovered!=envelope['signer'] or recovered not in bundle['committee']:raise ValueError('Unknown or mismatched witness')
        if recovered in signed:raise ValueError('Duplicate witness')
        signed[recovered]=envelope['signature']
    witnesses=sorted(signed,key=lambda a:int(a,16));signatures=[signed[a] for a in witnesses]
    return {'schema':'synafly.quorum-certificate.v1','bundle_hash':bundle_hash,'witnesses':witnesses,
            'signatures':signatures,'threshold':bundle['threshold'],
            'transaction':{'chainId':hex(bundle['chain_id']),'to':bundle['registry'],'value':'0x0',
                           'data':commit_calldata(abi_commitment(bundle['commitment']),signatures)}}
