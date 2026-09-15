#!/usr/bin/env python3
"""Prepare a replay-verified commitment for external witnesses; never broadcasts."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.anchor import ReadOnlyRPC,commitment
from verify_history import verify_bundle

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('bundle');p.add_argument('--contract',required=True);p.add_argument('--chain-id',type=int,choices=[56,97],required=True);a=p.parse_args()
    rpc_url=os.environ.get('BSC_RPC_URL')
    if not rpc_url:p.error('Set BSC_RPC_URL securely; its value is never printed')
    run,head,count=verify_bundle(a.bundle)
    prepared=ReadOnlyRPC(rpc_url,a.chain_id).prepare(a.contract,commitment(head));prepared['replayed_checkpoints']=count
    print(json.dumps(prepared,indent=2))
