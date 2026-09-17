#!/usr/bin/env python3
"""PUBLIC TEST SEED ONLY. Reproducible codec vectors; never fund these accounts."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.mainnet_plan import IDENTITY,plan
from synafly_lab.mainnet_signer import EnvironmentSigner
from synafly_lab.offload_receipts import digest,ZERO
from synafly_lab.mainnet_codec import decode_transaction
ROOT=Path(__file__).resolve().parents[1]


def generate():
    # Publicly specified test seed, not a secret, real wallet, or funded mnemonic.
    test_key=hashlib.sha256(b'SynaFly PR10 PUBLIC CODEC FIXTURE - NEVER FUND').hexdigest()
    signer=EnvironmentSigner({'BSC_MAINNET_PRIVATE_KEY':test_key})
    lineage=digest('SynaFly fixed verification genesis v1');graph=digest('SynaFly fixed test graph')
    model=digest('SynaFly fixed test receipt model');rows=[];parent=ZERO
    for i in range(3):
        row={'schema':'synafly.offload-receipt.v1','timestamp':1735689600000+i,'block_hash':'0x'+'11'*32,
             'target_contract':'0x'+'22'*20,'target_slot':hex(8+i),'upstream_saved':1,'source':'cache',
             'lineage':lineage,'sequence':i,'parent':parent}
        row['receipt_hash']=digest(row)
        row['commitment']={'lineage':lineage,'graph':graph,'model':model,'checkpoint':row['receipt_hash'],'parent':parent,'sequence':i,'tick':i}
        rows.append(row);parent=row['receipt_hash']
    artifact=json.loads((ROOT/'tests/fixtures/mainnet-build.json').read_text())['artifact']
    p=plan(artifact,ROOT,rows,signer.address,0,signer.committee,kind='codec-test-fixture')
    cert=signer.certificate(p['genesis_bundle'])
    base={'chainId':56,'value':0,'gasPrice':100000000}
    deployment=signer.transaction({**base,'nonce':0,'gas':1200000,'data':p['creation_data']})
    genesis=signer.transaction({**base,'nonce':1,'gas':300000,'to':bytes.fromhex(p['registry_address'][2:]),'data':cert['transaction']['data']})
    typed=signer.transaction({'type':2,'chainId':56,'nonce':1,'gas':300000,'maxFeePerGas':100000000,'maxPriorityFeePerGas':10000000,'to':bytes.fromhex(p['registry_address'][2:]),'value':0,'data':cert['transaction']['data'],'accessList':[]})
    for raw in [deployment,genesis,typed]:
        if decode_transaction(raw)['sender']!=signer.address:raise ValueError('Independent transaction recovery mismatch')
    return {**IDENTITY,'kind':'PUBLIC_CODEC_TEST_FIXTURE_NEVER_FUND','not_for_broadcast':True,
            'plan':p,'certificate':cert,'raw_deploy':deployment,'raw_genesis':genesis,'raw_type2':typed,
            'note':'Synthetic verification receipts and public test seed. Not live BSC offload, real deployment, or production signers.'}

if __name__=='__main__':
    value=generate();path=ROOT/'tests/fixtures/genesis_checkpoint.json';path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    print('Public synthetic fixture regenerated; no network, keys printed, or transactions broadcast.')
