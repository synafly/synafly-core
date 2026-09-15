#!/usr/bin/env python3
"""Independently replay an exported bundle without running the publisher's node."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.canonical import parse,digest,keys,Invalid
from synafly_lab.checkpoint import Run

def verify_bundle(path):
    raw=Path(path).read_bytes()
    if len(raw)>16*1024*1024:raise Invalid('Bundle size limit')
    bundle=parse(raw);keys(bundle,['spec','checkpoints']);spec=bundle['spec'];keys(spec,['schema','graph','model','seed'])
    run=Run(spec['graph'],spec['seed'],spec['model'])
    if run.spec!=spec:raise Invalid('Run specification mismatch')
    cps=bundle['checkpoints']
    if type(cps) is not list or not 1<=len(cps)<=2048:raise Invalid('History size')
    parent=None
    for cp in cps:run.verify(cp,parent);parent=cp
    return run,parent,len(cps)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('bundle');a=p.parse_args();run,head,count=verify_bundle(a.bundle)
    print(json.dumps({'verified_checkpoints':count,'run_id':run.id,'head_hash':digest(head),'state_hash':head['state_hash'],'tick':head['tick']}))
