#!/usr/bin/env python3
"""Deterministic graph lookup experiment, not live traffic, WAN latency or savings."""
import argparse,hashlib,json,random,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.rpc import encode
from synafly_lab.topology import Overlay,conventional_overlay,owners,rewire
ROOT=Path(__file__).resolve().parents[1]

def run_benchmark(output):
    raw=(ROOT/'data/malecns-sample.json').read_bytes();graph=json.loads(raw);n=len(graph['nodes'])
    graphs=[('observed-malecns',graph,0)]
    for seed in range(8):
        rewired,swaps=rewire(graph,100+seed);graphs.append(('degree-matched-'+str(seed),rewired,swaps))
    graphs.append(('conventional-equal-edge-budget',conventional_overlay(graph),0))
    graphs.append(('direct-owner-reference',graph,0))
    # Ownership and request distribution are independent of the graph being tested.
    keys=['synthetic-pinned-rpc-key-'+str(i) for i in range(64)]
    ownership={key:owners(key,n,3) for key in keys}
    workloads=[]
    for seed in range(5):
        rng=random.Random(seed)
        queries=[(rng.randrange(n),keys[rng.randrange(len(keys))]) for _ in range(512)]
        permutation=list(range(n));random.Random(1000+seed).shuffle(permutation)
        workloads.append((seed,queries,permutation))
    reports=[]
    for name,candidate,swaps in graphs:
        overlay=Overlay(candidate);trials=[]
        for seed,queries,permutation in workloads:
            for percentage in [0,5,20]:
                failed=set(permutation[:n*percentage//100]);outcomes=[]
                for source,key in queries:
                    result=overlay.direct(source,ownership[key],failed) if name=='direct-owner-reference' else overlay.search(source,ownership[key],failed,budget=32,hops=8)
                    outcomes.append(result)
                histogram=[0]*9
                for result in outcomes:
                    if result['found']:histogram[result['hops']]+=1
                trial={'seed':seed,'failed_nodes':len(failed),'failure_scenario_percent':percentage,
                    'queries':len(queries),'cache_shard_hits':sum(r['found'] for r in outcomes),
                    'origin_fallbacks':sum(not r['found'] for r in outcomes),
                    'lookup_contact_attempts':sum(r['contacts'] for r in outcomes),
                    'failed_contact_attempts':sum(r['failed_contacts'] for r in outcomes),
                    'entry_failures':sum(r['entry_failed'] for r in outcomes),
                    'successful_hop_histogram':histogram,
                    'workload_sha256':hashlib.sha256(encode([queries,sorted(failed)])).hexdigest(),
                    'outcomes_sha256':hashlib.sha256(encode(outcomes)).hexdigest()}
                assert trial['cache_shard_hits']+trial['origin_fallbacks']==len(queries)
                assert trial['lookup_contact_attempts']<=32*len(queries)
                trials.append(trial)
        constrained=name!='direct-owner-reference'
        reports.append({'strategy':name,'nodes':n,'edges':len(candidate['edges']) if constrained else None,
            'successful_rewire_swaps':swaps,'connectivity':overlay.connectivity() if constrained else None,
            'graph_sha256':hashlib.sha256(encode(candidate)).hexdigest() if constrained else None,
            'edge_constrained':constrained,'trials':trials})
    report={'experiment':'bounded-cache-shard-lookup','kind':'controlled_graph_simulation',
        'source_graph_file_sha256':hashlib.sha256(raw).hexdigest(),
        'method':'Same graph-independent key owners, warm replica placement, entry points, seeds and failed nodes. BFS ignores weights, visits at most 32 distinct remote vertices and at most 8 hops. Failed probes count. No component filtering or biological-only repair edges.',
        'replicas_per_key':3,'keys':len(keys),'workload_seeds':5,'queries_per_seed':512,
        'reference_boundary':'Direct-owner lookup knows the same owner directory but is not constrained by graph edges. It is a practical reference, not an equal-edge-budget control.',
        'limits':['Uniform synthetic entry points and keys; not measured BSC traffic.',
                  'Warm logical cache replicas; no eviction or CPU/network timing model.',
                  'Contacts are abstract attempts, not measured WAN packets or bytes.',
                  'Sample is biased and incomplete; conclusions do not generalize to a full connectome.',
                  'Simulator is separate from the HTTP edge; no biological routing benefit is built into the gateway.'],
        'results':reports,
        'implementation_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['synafly_lab/topology.py','scripts/benchmark_routing.py']}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    summary=[]
    for row in reports:
        if row['strategy'].startswith('degree-matched-') and row['strategy']!='degree-matched-0':continue
        trials=[t for t in row['trials'] if t['failed_nodes']==0]
        summary.append({'strategy':row['strategy'],'queries':sum(t['queries'] for t in trials),
                        'hits':sum(t['cache_shard_hits'] for t in trials),
                        'fallbacks':sum(t['origin_fallbacks'] for t in trials),
                        'contacts':sum(t['lookup_contact_attempts'] for t in trials)})
    print(json.dumps({'kind':report['kind'],'zero_failure_summary':summary},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='results/routing-benchmark.json');a=p.parse_args();run_benchmark(a.out)
