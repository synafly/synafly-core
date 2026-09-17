#!/usr/bin/env python3
"""Export bounded runtime hints from the recorded PR #7 SYNTHETIC Anvil training set.

This does not train a mainnet/PancakeSwap predictor or enable online trace learning.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.rpc import encode,load_json,NetworkIdentity
from synafly_lab.recipe_prefetch import load_catalog
from synafly_lab.keccak import keccak256
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='.local/synthetic-daemon-recipes.json');args=p.parse_args()
    corpus=json.loads((ROOT/'data/evm-access-recipes.json').read_text())
    rows=[]
    for parsed in corpus['parsed_recipes']:
        recipe=parsed['recipe']
        if any(kind!='sload' for kind,_ in recipe['accesses']):continue
        training=corpus['training'][parsed['training_row']]
        if '0x'+keccak256(bytes.fromhex(training['code'][2:])).hex()!=training['code_hash']:raise ValueError('Code hash mismatch')
        rows.append({'code_hash':training['code_hash'],'selector':training['data'][:10],
                     'data':training['data'],'caller':training['caller'],'value':'0x0','recipe':recipe})
    # This corpus was trained on owned Anvil. Require operators to provide their
    # own reviewed network-bound catalog for BSC; never silently relabel the data.
    identity=NetworkIdentity(1337,'0x'+'01'*32)
    value={'schema':'synafly.daemon-recipes.v1','chain_id':identity.chain_id,'genesis_hash':identity.genesis_hash,'entries':rows}
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(value)+b'\n')
    load_catalog(path,identity)
    print(f'Exported {len(rows)} synthetic training observations. Fixture network only, not mainnet.')
if __name__=='__main__':main()
