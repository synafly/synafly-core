#!/usr/bin/env python3
"""Offline PR10 source/fixture audit; never verifies or broadcasts a live deployment."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.mainnet_plan import IDENTITY,validate_plan,TOKEN_CA
from synafly_lab.mainnet_pipeline import check_pair,fee_wei
ROOT=Path(__file__).resolve().parents[1]


def audit():
    lock=json.loads((ROOT/'results/mainnet-tooling-release-lock.json').read_text())
    if lock['schema']!='synafly.mainnet-tooling-lock.v1':raise ValueError('Lock schema')
    for name,expected in lock['sha256'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Lock path')
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=expected:raise ValueError('Source binding: '+name)
    fixture=json.loads((ROOT/'tests/fixtures/genesis_checkpoint.json').read_text())
    artifact=json.loads((ROOT/'tests/fixtures/mainnet-build.json').read_text())['artifact']
    validate_plan(fixture['plan'],artifact,ROOT)
    check_pair(fixture['plan'],fixture['certificate'],fixture['raw_deploy'],fixture['raw_genesis'],fee_wei('0.002'))
    check_pair(fixture['plan'],fixture['certificate'],fixture['raw_deploy'],fixture['raw_type2'],fee_wei('0.002'))
    if fixture['not_for_broadcast'] is not True:raise ValueError('Fixture must not become a production wallet')
    record=json.loads((ROOT/'deployments/bsc-mainnet.json').read_text())
    if any(record.get(k)!=v for k,v in IDENTITY.items()):raise ValueError('Registry/token isolation metadata')
    if record['status']=='not_deployed' and any(record.get(k) is not None for k in ['registry_address','deploy_tx_hash','genesis_commit_tx_hash','block_number']):raise ValueError('Undeployed status with fabricated transaction fields')
    return {**IDENTITY,'source_bindings':'matched','chain56_fixture':'verified','operational_record_status':record['status'],
            'live_mainnet_verified':False,'public_transactions_sent':0}

if __name__=='__main__':print(json.dumps(audit(),sort_keys=True))
