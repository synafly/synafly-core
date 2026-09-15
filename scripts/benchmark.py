#!/usr/bin/env python3
"""Reproducible ablation, not evidence of BSC node replacement or biological fidelity."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.canonical import parse,digest,canonical
from synafly_lab.checkpoint import Run
from synafly_lab.model import stimulus
from synafly_lab.topology import rewire
ROOT=Path(__file__).resolve().parents[1]

def run_benchmark(out):
    graph=parse((ROOT/'data/malecns-sample.json').read_bytes());shuffled,swaps=rewire(graph,42);empty=copy.deepcopy(graph);empty['edges']=[]
    reports=[];traces={}
    for name,g in [('observed-malecns',graph),('degree-matched-rewired',shuffled),('no-edges-ablation',empty)]:
        fingerprints=[];spike_counts=[];transmissions=[]
        for seed in ['trial-0','trial-1','trial-2','trial-3','trial-4']:
            run=Run(g,seed);state=run.circuit.initial();trace=[];tx=0
            degrees=[0]*run.circuit.n
            for a,b,w in g['edges']:degrees[a]+=1
            for tick in range(256):
                tx+=sum(degrees[i] for i in state['spikes']);state=run.circuit.step(state,stimulus(seed,tick,run.circuit.n))
                if seed=='trial-0':trace.append({'tick':state['tick'],'spikes':state['spikes'],'state_hash':digest(state)})
            fingerprints.append(digest(state));spike_counts.append(state['total_spikes']);transmissions.append(tx)
            if seed=='trial-0':traces[name]=trace
        reports.append({'strategy':name,'nodes':len(g['nodes']),'edges':len(g['edges']),'ticks':256,'seeds':5,'all_state_hashes':fingerprints,'all_spike_counts':spike_counts,'all_synapse_transmissions':transmissions})
    assert reports[0]['all_state_hashes']!=reports[1]['all_state_hashes']
    assert reports[0]['all_state_hashes']!=reports[2]['all_state_hashes']
    report={'experiment':'toy-model-topology-ablation','runtime_requirement':'Python >=3.12','graph_file_sha256':hashlib.sha256((ROOT/'data/malecns-sample.json').read_bytes()).hexdigest(),'rewire_seed':42,'successful_swaps':swaps,'method':'Same stimuli, neuron count and dynamics. Rewired graph matches edge count and in/out degree; no-edges is an ablation, NOT an efficiency baseline.','measurement_scope':'Deterministic state and synapse-event outcomes only; no hardware timing or routing-performance claim.','established':'Graph edges affect model dynamics; neither usefulness nor superiority is established.','results':reports}
    out=Path(out);out.mkdir(parents=True,exist_ok=True);(out/'ablation.json').write_text(json.dumps(report,indent=2)+'\n');(out/'replay.json').write_text(json.dumps(traces,separators=(',',':'))+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='results');a=p.parse_args();run_benchmark(a.out)
