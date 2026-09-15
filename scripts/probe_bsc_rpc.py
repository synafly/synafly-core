#!/usr/bin/env python3
"""Opt-in, bounded public BSC read compatibility probe. Never sends transactions."""
import argparse,concurrent.futures,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from synafly_lab.edge import ReadEdge
from synafly_lab.rpc import BSC_GENESIS,HttpOrigin,NetworkIdentity,RpcError,encode,hex_data,quantity

def _probe(upstream,progress,clients):
    identity=NetworkIdentity(56,BSC_GENESIS)
    progress.update(phase='capture bootstrap',bootstrap_in_progress=True)
    capture=HttpOrigin(upstream,identity,timeout=8)
    clients.append(capture)
    modes=[]
    for name,capacity in [('pass-through',0),('pinned-cache',128)]:
        progress.update(phase=name+' bootstrap',bootstrap_in_progress=True)
        client=HttpOrigin(upstream,identity,timeout=8);clients.append(client)
        modes.append((name,client,ReadEdge(client,cache_entries=capacity,coalesce=False),[]))
    progress.update(phase='capture block',bootstrap_in_progress=False)
    block=capture.call('eth_getBlockByNumber',['latest',False])
    if type(block) is not dict:raise ValueError('Invalid block response')
    block_hash=hex_data(block.get('hash'),32);number=quantity(block.get('number'),64)
    progress['queried_block']={'number':number,'hash':block_hash,'require_canonical':False}
    selector={'blockHash':block_hash,'requireCanonical':False}
    address='0x'+'0'*40  # Public neutral probe address; never a user's wallet.
    reads=[('eth_getBalance',[address,selector]),('eth_getCode',[address,selector]),
           ('eth_getTransactionCount',[address,selector]),('eth_getStorageAt',[address,'0x0',selector])]
    def pair(item):
        round_index,method,params=item;values=[]
        for name,client,edge,_ in modes:
            try:values.append({'method':method,'result':edge.read(method,params)})
            except RpcError as exc:
                exc.probe_phase=name+': '+method+': round '+str(round_index)
                raise
        assert values[0]==values[1]
        return values
    # Capture the block after bootstrap and pair each baseline/cache query. This
    # reduces observation-time skew; it is not a latency or throughput benchmark.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for round_index in range(4):
            progress['phase']='paired reads: round '+str(round_index)
            for values in pool.map(pair,[(round_index,method,params) for method,params in reads]):
                for mode,value in zip(modes,values):mode[3].append(value)
    results=[]
    for name,client,edge,values in modes:
        results.append({'mode':name,'client_reads':len(values),'values':values[:4],
                        'outcomes_sha256':hashlib.sha256(encode(values)).hexdigest(),
                        'edge_metrics':edge.stats(),'origin_metrics':client.counters()})
    assert results[0]['outcomes_sha256']==results[1]['outcomes_sha256']
    total=capture.counters()['rpc_calls']+sum(row['origin_metrics']['rpc_calls'] for row in results)
    assert total==27
    report={'experiment':'public-bsc-fixed-block-read-compatibility','status':'passed',
        'upstream':capture.url,'chain_id':56,'genesis_hash':BSC_GENESIS,
        'queried_block':{'number':number,'hash':block_hash,'require_canonical':False},
        'address':address,'repeat_rounds':4,'query_profile':'Four read methods, one neutral address, one explicit block hash.',
        'schedule':'Block captured after identity bootstrap; four paired method workers per round; no retries. Not a latency benchmark.',
        'shared_capture_rpc_calls':capture.counters()['rpc_calls'],'total_public_rpc_calls':total,
        'blockchain_transactions_sent':0,'public_registry_deployed':False,
        'results_match':True,'results':results,
        'limits':['One trusted public provider and controlled repeated reads; not real user traffic or an independent consensus proof.',
                  'Historical state may be pruned later; rerunning selects a new block and can produce different values.',
                  'This is a read-only compatibility observation, not a WAN-scale benchmark or cost-saving estimate.',
                  'The biological graph does not participate in this cache probe.']}
    return report

def run_probe(upstream,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    # A failed rerun must not leave an older successful report looking current.
    output.write_text(json.dumps({'status':'running','experiment':'public-bsc-fixed-block-read-compatibility'})+'\n')
    progress={};clients=[]
    try:
        report=_probe(upstream,progress,clients)
    except (RpcError,AssertionError,ValueError) as exc:
        report={'experiment':'public-bsc-fixed-block-read-compatibility','status':'failed',
            'phase':getattr(exc,'probe_phase',progress.get('phase','configuration')),'error_code':getattr(exc,'code',None),
            'upstream':clients[0].url if clients else None,'queried_block':progress.get('queried_block'),
            'failure':exc.message if isinstance(exc,RpcError) else 'Response validation or comparison failed',
            'known_origin_rpc_calls':sum(c.counters()['rpc_calls'] for c in clients),
            'failed_bootstrap_call_count_unavailable':progress.get('bootstrap_in_progress',False),
            'blockchain_transactions_sent':0}
        output.write_text(json.dumps(report,indent=2)+'\n')
        raise
    report['implementation_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ['synafly_lab/rpc.py','synafly_lab/edge.py','scripts/probe_bsc_rpc.py']}
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':'passed','chain_id':56,'block':report['queried_block']['number'],'total_public_rpc_calls':report['total_public_rpc_calls'],
                     'state_reads_by_mode':{r['mode']:r['origin_metrics']['state_reads'] for r in report['results']},
                     'transactions_sent':0},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--upstream',required=True)
    p.add_argument('--out',default='results/bsc-read-probe.json');a=p.parse_args()
    try:run_probe(a.upstream,a.out)
    except (RpcError,AssertionError,ValueError):
        print('Read probe failed; the output report records the failure, not a cached success.',file=sys.stderr)
        raise SystemExit(1)
