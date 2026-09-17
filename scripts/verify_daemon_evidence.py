#!/usr/bin/env python3
"""Verify release file bindings, receipt chains and daemon stress accounting offline."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.offload_receipts import digest, ZERO
ROOT=Path(__file__).resolve().parents[1]


def verify():
    lock=json.loads((ROOT/'results/edge-daemon-release-lock.json').read_text())
    for name,expected in lock['sha256'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Lock path')
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=expected:raise ValueError('Source/report binding: '+name)
    report=json.loads((ROOT/'results/edge-daemon-verification.json').read_text())
    for name in ['mesh_peer_hit','peer_exit_fallback','foreground_parity','proactive_before_first_client']:
        if report[name] is not True:raise ValueError('Process evidence')
    for key in ['mesh_metrics','warmer_metrics']:
        receipts=report[key]['receipts'];rows=receipts['retained']
        parent=ZERO
        for i,row in enumerate(rows):
            core={k:v for k,v in row.items() if k not in {'receipt_hash','commitment'}}
            if row['receipt_hash']!=digest(core) or row['parent']!=parent or row['sequence']!=i or row['upstream_saved']!=1:raise ValueError('Receipt chain')
            c=row['commitment']
            if c['parent']!=parent or c['checkpoint']!=row['receipt_hash'] or c['sequence']!=i or c['tick']!=i:raise ValueError('Commitment mapping')
            parent=row['receipt_hash']
        if receipts['head']!=parent:raise ValueError('Receipt head')
    stress=json.loads((ROOT/'results/edge-daemon-stress.json').read_text())
    for row in stress['rows']:
        for mode in ['edge','direct']:
            counts=row[mode]['responses']
            outcomes=sum(counts.get(k,0) for k in ['correct','rpc_errors','http_503','transport_errors'])
            if outcomes!=row[mode]['attempted_clients'] or counts.get('wrong_values') or counts.get('bad_id'):raise ValueError('Stress outcomes')
        metrics=row['edge_metrics'];lanes=metrics['upstream']['lanes']
        if metrics['client'].get('success',0)!=row['edge']['responses'].get('correct',0):raise ValueError('Success accounting')
        if lanes.get('foreground:eth_getStorageAt',0)!=row['edge_origin_reads']:raise ValueError('Origin accounting')
        if row['rejected_requests_counted_as_offload'] is not False:raise ValueError('Rejected-as-offload claim')
        if metrics['cache']['cache_entries']>1024 or metrics['cache']['cache_value_bytes']>4*1024*1024:raise ValueError('Cache bound')
        if metrics['client'].get('errors') and metrics['net_rpc_reduction_percent'] is not None:raise ValueError('Errors cannot be savings')
    return {'bindings':'matched','process_checks':'matched','stress_accounting':'matched','network_used':False}

if __name__=='__main__':print(json.dumps(verify()))
