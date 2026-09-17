#!/usr/bin/env python3
"""Prepare/collect/verify certificates. Automatic writes only through the local-anvil subcommand."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.quorum_relayer import build_checkpoint,aggregate_signatures,validate_bundle
from synafly_lab.quorum_rpc import ReadRPC,preflight,verify_transaction
from synafly_lab.rpc import encode,load_json


def load(path):
    with Path(path).open('rb') as stream:return load_json(stream.read(1048577),1048576)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    local=sub.add_parser('local-anvil');local.add_argument('--out',default='.local/quorum-pipeline.json')
    prepare=sub.add_parser('prepare');prepare.add_argument('--receipts',required=True);prepare.add_argument('--registry',required=True)
    prepare.add_argument('--runtime-code-hash',required=True);prepare.add_argument('--committee',nargs='+',required=True)
    prepare.add_argument('--chain-id',type=int,choices=[97],default=97);prepare.add_argument('--threshold',type=int,default=2);prepare.add_argument('--previous');prepare.add_argument('--out',required=True)
    collect=sub.add_parser('collect');collect.add_argument('--bundle',required=True);collect.add_argument('--signatures',required=True);collect.add_argument('--previous');collect.add_argument('--out',required=True)
    inspect=sub.add_parser('preflight');inspect.add_argument('--bundle',required=True);inspect.add_argument('--previous');inspect.add_argument('--rpc',required=True);inspect.add_argument('--out',required=True)
    verify=sub.add_parser('verify');verify.add_argument('--bundle',required=True);verify.add_argument('--signatures',required=True);verify.add_argument('--previous');verify.add_argument('--rpc',required=True);verify.add_argument('--tx-hash',required=True);verify.add_argument('--confirmations',type=int,default=3);verify.add_argument('--out',required=True)
    args=p.parse_args();previous=load(args.previous) if getattr(args,'previous',None) else None
    if args.mode=='local-anvil':
        from verify_quorum_pipeline import live_local
        result=live_local()
    elif args.mode=='prepare':
        snapshot=load(args.receipts)
        rows=snapshot['retained'] if isinstance(snapshot,dict) else snapshot
        result=build_checkpoint(rows,chain_id=args.chain_id,registry=args.registry,committee=sorted(args.committee,key=lambda a:int(a,16)),threshold=args.threshold,runtime_code_hash=args.runtime_code_hash,previous=previous)
    else:
        bundle=load(args.bundle);validate_bundle(bundle,previous)
        if bundle['chain_id']!=97:p.error('This CLI only prepares/verifies chain ID 97; no public mainnet workflow')
        if args.mode=='preflight':result=preflight(ReadRPC(args.rpc),bundle,previous)
        else:
            certificate=aggregate_signatures(bundle,load(args.signatures),previous)
            if args.mode=='collect':result=certificate
            else:result=verify_transaction(ReadRPC(args.rpc),bundle,certificate,args.tx_hash,confirmations=args.confirmations)
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(result)+b'\n')
    print(json.dumps({'mode':args.mode,'public_broadcast_performed':False,'result':'complete'}))
if __name__=='__main__':main()
