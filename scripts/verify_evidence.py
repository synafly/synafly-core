#!/usr/bin/env python3
"""Check report accounting, source bindings and optional offline reproduction."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def load(name):return json.loads((ROOT/'results'/name).read_text())

def bindings(report):
    items=report['implementation_sha256']
    assert items and type(items) is dict
    for name,expected in items.items():
        path=Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==expected, 'Report implementation changed: '+name

def verify(edge_path=None,routing_path=None):
    edge=load('edge-http.json');routing=load('routing-benchmark.json');public=load('bsc-read-probe.json')
    for report in [edge,routing,public]:bindings(report)
    assert edge['public_network_used'] is False and edge['blockchain_transactions_sent']==0
    assert edge['all_modes_return_identical_outcomes'] is True
    assert {r['mode'] for r in edge['results']}=={'pass-through','cache-only','coalescing-only','cache-and-coalescing'}
    assert len({r['outcomes_sha256'] for r in edge['results']})==1
    for row in edge['results']:
        m=row['measured'];origin=m['origin']
        assert row['client_requests']==m['edge']['read_requests']==38
        assert origin['rpc_calls']==origin['state_reads']+origin['metadata_calls']
        assert origin['metadata_calls']==2 and m['edge']['inflight_keys']==0
        assert row['all_expected_responses_match'] is True
    assert routing['kind']=='controlled_graph_simulation' and len(routing['results'])==11
    assert hashlib.sha256((ROOT/'data/malecns-sample.json').read_bytes()).hexdigest()==routing['source_graph_file_sha256']
    signatures=None
    for row in routing['results']:
        assert row['nodes']==256
        if row['edge_constrained']:assert row['edges']==605
        else:assert row['strategy']=='direct-owner-reference' and row['edges'] is None
        keys={(t['seed'],t['failure_scenario_percent']):t['workload_sha256'] for t in row['trials']}
        assert len(keys)==15
        if signatures is None:signatures=keys
        assert keys==signatures,'Strategies did not receive identical workload/failure inputs'
        for trial in row['trials']:
            assert trial['cache_shard_hits']+trial['origin_fallbacks']==trial['queries']==512
            assert trial['lookup_contact_attempts']<=32*trial['queries']
            assert sum(trial['successful_hop_histogram'])==trial['cache_shard_hits']
    assert public['status']=='passed' and public['chain_id']==56
    assert public['blockchain_transactions_sent']==0 and public['public_registry_deployed'] is False
    assert len({r['outcomes_sha256'] for r in public['results']})==1
    assert public['total_public_rpc_calls']==public['shared_capture_rpc_calls']+sum(r['origin_metrics']['rpc_calls'] for r in public['results'])==27
    for row in public['results']:
        assert row['client_reads']==16 and row['edge_metrics']['coalesced_waiters']==0
    for path,expected in [(edge_path,edge),(routing_path,routing)]:
        if path is not None:assert json.loads(Path(path).read_text())==expected,'Offline reproduction differs: '+str(path)
    return {'source_bindings':'matched','accounting':'matched','offline_reproductions_compared':int(edge_path is not None)+int(routing_path is not None),
            'public_network_rerun':False,'scope':'Report checks are not an independent public-chain or scientific correctness proof.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--edge');p.add_argument('--routing');a=p.parse_args()
    print(json.dumps(verify(a.edge,a.routing)))
