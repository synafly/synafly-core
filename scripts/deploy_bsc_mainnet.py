#!/usr/bin/env python3
"""Mainnet infrastructure log only: offline dry-run or explicitly authorized broadcast.

NOT A TOKEN. No keys in arguments/logs/files. This script never upgrades packages.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.mainnet_plan import IDENTITY,DEFAULT_RPC,plan,validate_plan,check_build
from synafly_lab.mainnet_pipeline import MainnetRPC,broadcast,fee_wei,network_check,check_pair,atomic_json
from synafly_lab.mainnet_signer import EnvironmentSigner
from synafly_lab.rpc import load_json,quantity
ROOT=Path(__file__).resolve().parents[1]


def load(path,limit=2097152):
    with Path(path).open('rb') as f:return load_json(f.read(limit+1),limit)


def artifact(path=None):
    if path:return load(path)['artifact']
    # The compiler subprocess must not inherit a funded wallet environment.
    clean={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT') if k in os.environ}
    result=subprocess.run(['forge','build','--offline'],cwd=ROOT,env=clean,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    if result.returncode:raise ValueError('Offline compilation failed; install/cache the pinned compiler without wallet secrets first')
    return load(ROOT/'out/ContinuityRegistry.sol/ContinuityRegistry.json')


def publish_claims(record,path):
    if record.get('status')!='deployed' or record.get('chain_id')!=56 or record.get('confirmations_at_check',0)<3:raise ValueError('Cannot publish unconfirmed deployment claims')
    path=Path(path);text=path.read_text();lines=text.splitlines()
    matches=[i for i,line in enumerate(lines) if line.startswith('| BSC public-chain checkpoint exists |')]
    if len(matches)!=1:raise ValueError('Claims status row changed; review manually without rebroadcasting')
    lines[matches[0]]='| BSC public-chain checkpoint exists | Deployed permissioned log, no economic claim | Confirmed mainnet deployment/genesis are recorded in deployments/bsc-mainnet.json. Infrastructure log only; NOT the ecosystem token. |'
    path.write_text('\n'.join(lines)+'\n')


class RedactedParser(argparse.ArgumentParser):
    def error(self,message):
        self.print_usage(sys.stderr)
        self.exit(2,'Invalid arguments (values redacted). Use --help; pass environment variable names, never keys.\n')


def main():
    p=RedactedParser(prog='deploy_bsc_mainnet.py',description=__doc__)
    p.add_argument('mode',choices=['dry-run','broadcast'])
    p.add_argument('--rpc',default=DEFAULT_RPC)
    p.add_argument('--private-key-env',metavar='ENVIRONMENT_VARIABLE_NAME')
    p.add_argument('--witness-mode',choices=['derived','loaded'],default='derived')
    p.add_argument('--deployer');p.add_argument('--committee',nargs=3);p.add_argument('--nonce',type=int)
    p.add_argument('--artifact',help='Explicit offline compiler artifact; exact pinned bytes are still checked')
    p.add_argument('--fixture',action='store_true',help='Public test vector: dry-run only, never fund or broadcast')
    p.add_argument('--online',action='store_true',help='Dry-run only: check chain/nonce and estimate deploy gas')
    p.add_argument('--plan');p.add_argument('--certificate');p.add_argument('--raw-deploy');p.add_argument('--raw-genesis')
    p.add_argument('--gas-price-wei',type=int);p.add_argument('--deploy-gas',type=int,default=1200000);p.add_argument('--commit-gas',type=int,default=300000)
    p.add_argument('--max-total-fee-bnb',default='0.002');p.add_argument('--confirm-mainnet',action='store_true')
    p.add_argument('--journal',default='.local/bsc-mainnet-journal.json')
    p.add_argument('--out',help='Default: .local/mainnet-plan.json (dry-run), deployments/bsc-mainnet.json (broadcast)')
    a=p.parse_args()
    if a.mode=='broadcast' and (not a.confirm_mainnet or a.fixture):p.error('Broadcast requires --confirm-mainnet and refuses public test fixtures')
    if a.private_key_env and any([a.raw_deploy,a.raw_genesis,a.deployer,a.committee]):p.error('Choose environment signer OR public/offline-signed inputs')
    if a.fixture and (a.private_key_env or a.online or a.plan):p.error('Fixture dry-run is offline and cannot use wallet inputs')
    if a.gas_price_wei is not None and a.gas_price_wei<=0:p.error('Gas price must be positive')
    if not 21000<=a.deploy_gas<=2000000 or not 21000<=a.commit_gas<=500000:p.error('Gas limits outside reviewed bounds')
    maximum=fee_wei(a.max_total_fee_bnb)
    # Merely importing the module does not read any wallet environment variable.
    signer=EnvironmentSigner(os.environ,a.private_key_env,a.witness_mode) if a.private_key_env else None
    build=artifact(a.artifact);check_build(build,ROOT)
    fixture=load(ROOT/'tests/fixtures/genesis_checkpoint.json')
    rpc=MainnetRPC(a.rpc) if a.online or a.mode=='broadcast' else None
    if rpc:network_check(rpc)
    checkpoint_plan=load(a.plan) if a.plan else None
    if checkpoint_plan and 'approved_plan' in checkpoint_plan:checkpoint_plan=checkpoint_plan['approved_plan']
    saved=load(a.journal) if a.mode=='broadcast' and Path(a.journal).exists() else None
    if saved and checkpoint_plan is None and 'approved_plan' in saved:checkpoint_plan=saved['approved_plan']
    if a.fixture:checkpoint_plan=fixture['plan']
    if checkpoint_plan is None:
        deployer=signer.address if signer else a.deployer;committee=signer.committee if signer else a.committee
        if not deployer or not committee:p.error('Provide --private-key-env or public --deployer and --committee')
        nonce=a.nonce
        if nonce is None and rpc:nonce=int(quantity(rpc.call('eth_getTransactionCount',[deployer,'pending'])),16)
        if nonce is None:p.error('Offline dry-run needs an explicit --nonce')
        checkpoint_plan=plan(build,ROOT,fixture['plan']['genesis_bundle']['receipts'],deployer,nonce,committee)
    validate_plan(checkpoint_plan,build,ROOT)
    if signer and (signer.address!=checkpoint_plan['deployer'] or signer.committee!=checkpoint_plan['committee']):raise ValueError('Signer identity differs from saved plan')
    cert=signer.certificate(checkpoint_plan['genesis_bundle']) if signer else load(a.certificate) if a.certificate else fixture['certificate'] if a.fixture else None
    if cert and 'certificate' in cert:cert=cert['certificate']
    if a.mode=='dry-run':
        estimate=None
        if rpc:
            estimate=int(quantity(rpc.call('eth_estimateGas',[{'from':checkpoint_plan['deployer'],'data':checkpoint_plan['creation_data'],'value':'0x0'}])),16)
        reference=load(ROOT/'results/quorum-pipeline.json')
        local_gas={'deploy':int(reference['deployment_receipt']['gasUsed'],16),'genesis':reference['checkpoints'][0]['verification']['gas_used'],'source':'PR9 LOCAL ANVIL ONLY; not a mainnet estimate'}
        result={**IDENTITY,'status':'dry_run_only','approved_plan':checkpoint_plan,'certificate':cert,
                'deploy_gas_estimate':estimate,'genesis_gas_estimate':None,'local_evm_gas_reference':local_gas,
                'configured_gas_limits':{'deploy':a.deploy_gas,'commit':a.commit_gas},'max_total_fee_wei':maximum,
                'estimate_note':'Offline limits are not measured estimates; genesis estimation requires the deployed code.',
                'verify_command':checkpoint_plan['verify_command'],'public_transactions_sent':0}
        atomic_json(a.out or ROOT/'.local/mainnet-plan.json',result)
        print(json.dumps({**IDENTITY,'status':'dry_run_only','registry_address':checkpoint_plan['registry_address'],'verify_command':checkpoint_plan['verify_command'],'deploy_gas_estimate':estimate,'public_transactions_sent':0}))
        return
    if not cert:raise ValueError('Provide the actual registry-bound quorum certificate or environment signer')
    if signer:
        price=a.gas_price_wei or (saved.get('gas_price_wei') if saved else None) or int(quantity(rpc.call('eth_gasPrice',[])),16)
        if type(price) is not int or price<=0:raise ValueError('Gas price must be positive')
        gas_deploy=saved.get('deploy_gas',a.deploy_gas) if saved else a.deploy_gas
        gas_commit=saved.get('commit_gas',a.commit_gas) if saved else a.commit_gas
        if (gas_deploy+gas_commit)*price>maximum:raise ValueError('Gas limits and gas price exceed total BNB cap')
        common={'chainId':56,'value':0,'gasPrice':price}
        raw1=signer.transaction({**common,'nonce':checkpoint_plan['nonce'],'gas':gas_deploy,'data':checkpoint_plan['creation_data']})
        raw2=signer.transaction({**common,'nonce':checkpoint_plan['nonce']+1,'gas':gas_commit,'to':bytes.fromhex(checkpoint_plan['registry_address'][2:]),'data':cert['transaction']['data']})
    else:
        if not a.raw_deploy or not a.raw_genesis:raise ValueError('Two offline-signed raw transaction files required')
        def read_raw(path):
            with Path(path).open() as f:value=f.read(131077)
            if len(value)>131076:raise ValueError('Signed transaction file exceeds bound')
            return value.strip()
        raw1=read_raw(a.raw_deploy);raw2=read_raw(a.raw_genesis)
        first,second,_=check_pair(checkpoint_plan,cert,raw1,raw2,maximum);price=first['max_fee_per_gas'];gas_deploy=first['gas_limit'];gas_commit=second['gas_limit']
    hints={'approved_plan':checkpoint_plan,'gas_price_wei':price,'deploy_gas':gas_deploy,'commit_gas':gas_commit}
    result=broadcast(rpc,checkpoint_plan,cert,raw1,raw2,maximum,a.journal,a.out or ROOT/'deployments/bsc-mainnet.json',build,ROOT,journal_hints=hints)
    publish_claims(result,ROOT/'docs/claims.md')
    print(json.dumps({**IDENTITY,'status':result['status'],'registry_address':result['registry_address'],'deploy_tx_hash':result['deploy_tx_hash'],'genesis_commit_tx_hash':result['genesis_commit_tx_hash'],'bscscan_verification_status':result['bscscan_verification_status'],'verify_command':checkpoint_plan['verify_command']}))

if __name__=='__main__':
    try:main()
    except Exception as error:
        # Never dump exception repr, traceback locals, SDK key objects or RPC payloads.
        print('Mainnet pipeline stopped ('+type(error).__name__+'). No automatic resubmission. Review the local journal and configuration.',file=sys.stderr)
        sys.exit(1)
