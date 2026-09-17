"""Offline socket-level daemon, mesh and proactive warmer verification."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from synafly_lab.edge_daemon import DaemonOrigin, EdgeDaemon, parse_call, serve
from synafly_lab.edge_mesh import MeshOrigin, load_topology
from synafly_lab.offload_receipts import digest, ZERO
from synafly_lab.recipe_prefetch import load_catalog
from synafly_lab.keccak import keccak256
from synafly_lab.rpc import RpcError, encode, load_json
from daemon_fixture import WireOrigin, IDENTITY, ADDRESS, PIN, BLOCK, BLOCK_B, SELECTOR, TOKEN, catalog_document

ROOT=Path(__file__).resolve().parents[1]


def rpc(port,method,params=None,identifier=1,headers=None,path='/'):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=8)
    try:
        connection.request('POST',path,encode({'jsonrpc':'2.0','id':identifier,'method':method,'params':params or []}),{'Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse();raw=response.read()
        return response.status,load_json(raw) if raw else None
    finally:connection.close()


def wait_until(predicate):
    deadline=time.monotonic()+5
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Bounded wait expired')
        time.sleep(.005)


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.wire=WireOrigin();self.wire.__enter__();self.addCleanup(self.wire.__exit__)
        self.origin=DaemonOrigin(self.wire.url,IDENTITY,timeout=.5)
        self.daemons=[];self.servers=[]
        self.addCleanup(self.close)
    def close(self):
        if self.wire.gate:self.wire.gate.set()
        for server,thread in self.servers:server.shutdown();server.server_close();thread.join()
        for daemon in self.daemons:daemon.close()
    def daemon(self,**kwargs):
        daemon=EdgeDaemon(self.origin,**kwargs);self.daemons.append(daemon);return daemon
    def server(self,daemon,**kwargs):
        server=serve(daemon,0,**kwargs);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start();self.servers.append((server,thread));return server.server_port
    def catalog(self,value=None):
        path=Path(self.tmp.name)/'catalog.json';path.write_bytes(encode(value or catalog_document()));return load_catalog(path,IDENTITY)

    def test_all_six_methods_wire_and_write_denial(self):
        d=self.daemon();port=self.server(d)
        for method,params in [('eth_chainId',[]),('eth_blockNumber',[]),('eth_getBlockByNumber',['latest',False]),('eth_getCode',[ADDRESS,PIN]),('eth_getStorageAt',[ADDRESS,'0x8',PIN]),('eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN])]:
            status,result=rpc(port,method,params,'id');self.assertEqual(status,200);self.assertIn('result',result);self.assertEqual(result['id'],'id')
        before=self.origin.counters()['rpc_calls']
        for method in ['eth_sendRawTransaction','eth_sendTransaction','personal_sign','eth_sign','debug_traceCall','anvil_setCode','eth_getBalance']:
            self.assertEqual(rpc(port,method)[1]['error']['code'],-32601)
        self.assertEqual(self.origin.counters()['rpc_calls'],before)
        self.assertIsNone(d.metrics()['net_rpc_reduction_percent'])

    def test_singleflight_real_concurrent_http_and_receipts(self):
        d=self.daemon();port=self.server(d);self.wire.gate=threading.Event()
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs=[pool.submit(rpc,port,'eth_getStorageAt',[ADDRESS,'0x8',PIN],i) for i in range(8)]
            wait_until(lambda:d.edge.stats()['coalesced_waiters']==7);self.wire.gate.set()
            rows=[job.result() for job in jobs]
        self.assertEqual(self.wire.counts['eth_getStorageAt'],1)
        self.assertEqual({r[1]['id'] for r in rows},set(range(8)))
        self.assertEqual(len({r[1]['result'] for r in rows}),1)
        self.assertEqual(d.metrics()['foreground_avoidance_percent'],87.5)
        receipts=d.receipts.snapshot();self.assertEqual(receipts['total'],7)
        parent=ZERO
        for i,row in enumerate(receipts['retained']):
            core={k:v for k,v in row.items() if k not in {'receipt_hash','commitment'}}
            self.assertEqual(digest(core),row['receipt_hash']);self.assertEqual(row['parent'],parent)
            self.assertEqual(row['commitment']['tick'],i);self.assertEqual(row['commitment']['sequence'],i)
            self.assertEqual(row['commitment']['checkpoint'],row['receipt_hash']);parent=row['receipt_hash']

    def test_lru_ttl_bytes_and_receipt_retention(self):
        d=self.daemon(max_cache=2,cache_bytes=132,ttl=.02,receipt_capacity=2)
        for i in range(5):d.read('eth_getStorageAt',[ADDRESS,hex(i),PIN])
        self.assertEqual(d.edge.stats()['cache_entries'],2);self.assertEqual(d.edge.stats()['cache_value_bytes'],132)
        for _ in range(4):d.read('eth_getStorageAt',[ADDRESS,'0x4',PIN])
        self.assertEqual(len(d.receipts.snapshot()['retained']),2)
        time.sleep(.03);d.read('eth_getStorageAt',[ADDRESS,'0x4',PIN]);self.assertEqual(self.wire.counts['eth_getStorageAt'],6)

    def test_latest_and_canonical_bypass_and_fork_isolation(self):
        d=self.daemon(catalog=self.catalog())
        for selector in ['latest','pending','0x1',{'blockHash':BLOCK,'requireCanonical':True}]:
            for _ in range(2):d.read('eth_getStorageAt',[ADDRESS,'0x8',selector])
        self.assertEqual(self.wire.counts['eth_getStorageAt'],8)
        a=d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN]);self.wire.head=BLOCK_B
        b=d.read('eth_getStorageAt',[ADDRESS,'0x8',{'blockHash':BLOCK_B}]);self.assertNotEqual(a,b)
        with self.assertRaises(RpcError):d.read('eth_getStorageAt',[ADDRESS,'0x8',{'blockHash':BLOCK,'requireCanonical':True}])
        self.assertEqual(d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN]),a)
        d.read('eth_call',[{'to':ADDRESS,'data':SELECTOR},'latest']);self.assertEqual(d.prefetch.stats()['pending'],0)

    def test_prefetch_fills_slot_cache_but_preserves_eth_call(self):
        d=self.daemon(catalog=self.catalog());port=self.server(d)
        with patch('subprocess.run',side_effect=AssertionError('No cast')):
            value=rpc(port,'eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN])[1]['result']
            wait_until(lambda:d.prefetch.stats().get('slot_reads_completed')==1)
        before=self.origin.counters()['rpc_calls']
        slot=rpc(port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])[1]['result']
        self.assertEqual(value,slot);self.assertEqual(self.origin.counters()['rpc_calls'],before)
        metrics=d.metrics();self.assertEqual(metrics['upstream']['lanes']['prefetch'],2)
        self.assertEqual(metrics['net_rpc_reduction_percent'],-50)
        self.assertEqual(metrics['foreground_avoidance_percent'],50)

    def test_prefetch_unknown_code_abstains_and_errors_do_not_poison(self):
        d=self.daemon(catalog=self.catalog());self.wire.code='0x00'
        result=d.read('eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN]);wait_until(lambda:not d.prefetch.stats()['pending'])
        self.assertEqual(d.prefetch.stats()['abstentions'],1);self.assertEqual(self.wire.counts['eth_getStorageAt'],0)
        self.wire.fault='malformed'
        with self.assertRaises(RpcError):d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        self.assertIsNone(d.edge.peek('eth_getStorageAt',[ADDRESS,'0x8',PIN]))
        self.wire.fault=None;self.assertEqual(d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN]),result)

    def test_prefetch_budget_and_close(self):
        d=self.daemon(catalog=self.catalog(),prefetch_budget=1)
        for _ in range(5):d.read('eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN])
        d.close();self.assertEqual(d.prefetch.stats()['scheduled'],1);self.assertEqual(d.prefetch.stats()['pending'],0)
        self.assertEqual(d.prefetch.stats()['skipped_budget_or_duplicate'],4)

    def test_catalog_scope_and_ast_fail_closed(self):
        for mutate in [lambda x:x.update(chain_id=56),lambda x:x['entries'][0].update(code_hash='invalid'),lambda x:x['entries'][0]['recipe'].update(accesses=[['sstore',['const',8]]]),lambda x:x['entries'][0]['recipe'].update(guards=[[['bad'],True]])]:
            x=catalog_document();mutate(x)
            with self.assertRaises((ValueError,TypeError)):self.catalog(x)

    def test_call_parameter_profile(self):
        for params in [[{'to':ADDRESS,'data':'0x1'}], [{'to':ADDRESS,'gas':'0x1000000'}], [{'to':ADDRESS,'accessList':[]}], [{'to':ADDRESS,'data':SELECTOR,'input':SELECTOR}], [{'to':ADDRESS},'latest',{}], [{'data':SELECTOR}]]:
            with self.assertRaises(RpcError):parse_call(params)
        call,block=parse_call([{'to':ADDRESS,'input':SELECTOR,'from':ADDRESS,'value':'0x0'},PIN])
        self.assertEqual(call['input'],SELECTOR);self.assertTrue(block.cacheable)

    def test_http_host_origin_auth_batch_framing(self):
        d=self.daemon();port=self.server(d,token=TOKEN,origins=['https://example.org'])
        self.assertEqual(rpc(port,'eth_chainId')[0],401)
        auth={'Authorization':'Bearer '+TOKEN}
        self.assertEqual(rpc(port,'eth_chainId',headers={**auth,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(rpc(port,'eth_chainId',headers={**auth,'Host':'evil.example'})[0],403)
        self.assertEqual(rpc(port,'eth_chainId',headers={**auth,'Origin':'https://example.org'})[1]['result'],'0x539')
        connection=http.client.HTTPConnection('127.0.0.1',port)
        batch=[{'jsonrpc':'2.0','id':1,'method':'eth_chainId'},{'jsonrpc':'2.0','method':'eth_sendRawTransaction'}]
        connection.request('POST','/',encode(batch),{'Content-Type':'application/json',**auth});response=connection.getresponse();value=load_json(response.read());connection.close()
        self.assertEqual(len(value),1)
        self.assertEqual(self.wire.counts['eth_sendRawTransaction'],0)
        connection=http.client.HTTPConnection('127.0.0.1',port)
        connection.request('POST','/',b'x'*16385,{'Content-Type':'application/json',**auth});response=connection.getresponse();self.assertEqual(response.status,413);response.read();connection.close()

    def test_mesh_cache_hit_over_real_http_and_failure_fallback(self):
        topology=load_topology(ROOT/'data/mesh-topology.json');neighbor=topology[0][0]
        b_mesh=MeshOrigin(self.origin,neighbor,{},TOKEN,topology);b=self.daemon(mesh=b_mesh)
        peer_port=self.server(b,peer=True,token=TOKEN)
        b.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        a_mesh=MeshOrigin(self.origin,0,{neighbor:f'http://127.0.0.1:{peer_port}'},TOKEN,topology)
        a=self.daemon(mesh=a_mesh);port=self.server(a)
        before=self.wire.counts['eth_getStorageAt']
        self.assertIn('result',rpc(port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])[1])
        self.assertEqual(self.wire.counts['eth_getStorageAt'],before);self.assertEqual(a_mesh.stats()['hits'],1)
        self.assertEqual(a.receipts.snapshot()['retained'][0]['source'],'peer')
        a_mesh.token='x'*32
        self.assertIn('result',rpc(port,'eth_getStorageAt',[ADDRESS,'0x9',PIN])[1])
        self.assertEqual(a_mesh.stats()['failures'],1);self.assertEqual(self.wire.counts['eth_getStorageAt'],before+1)

    def test_mesh_auth_topology_key_and_network_denial(self):
        topology=load_topology(ROOT/'data/mesh-topology.json');neighbor=topology[0][0]
        mesh=MeshOrigin(self.origin,neighbor,{},TOKEN,topology);d=self.daemon(mesh=mesh);port=self.server(d,peer=True,token=TOKEN)
        self.assertEqual(rpc(port,'eth_chainId',path='/peer/read')[0],401)
        with self.assertRaises(ValueError):MeshOrigin(self.origin,0,{0:self.wire.url},TOKEN,topology)
        from synafly_lab.rpc import parse_read,cache_key
        query={'schema':'synafly.mesh-read.v1','chain_id':1337,'genesis_hash':IDENTITY.genesis_hash,'sender_role':0,'method':'eth_getStorageAt','params':[ADDRESS,'0x8',PIN]}
        query['key']=cache_key(IDENTITY,parse_read(query['method'],query['params']))
        before=self.origin.counters()['rpc_calls'];self.assertFalse(mesh.receive(query,'Bearer '+TOKEN)['hit'])
        self.assertEqual(before,self.origin.counters()['rpc_calls'])
        for k,v in [('key','invalid'),('chain_id',56),('method','eth_sendRawTransaction')]:
            with self.assertRaises(RpcError):mesh.receive({**query,k:v},'Bearer '+TOKEN)

    def test_block_warmer_precedes_client_read_and_repeated_head_skips(self):
        from synafly_lab.block_warmer import BlockWarmer
        d=self.daemon(catalog=self.catalog());self.wire.transactions=[{'to':ADDRESS,'input':SELECTOR}]
        warmer=BlockWarmer(d);warmer.poll();wait_until(lambda:d.prefetch.stats().get('slot_reads_completed')==1)
        self.assertEqual(d.metrics()['client'],{})
        before=self.origin.counters()['rpc_calls'];d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        self.assertEqual(self.origin.counters()['rpc_calls'],before)
        warmer.poll();self.assertEqual(warmer.stats()['blocks'],1)
        self.assertEqual(d.metrics()['upstream']['lanes']['watcher'],3)

    def test_cli_sigterm_exports_metrics_without_private_paths(self):
        report=Path(self.tmp.name)/'report.json'
        process=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_edge_daemon.py'),'--port','0','--upstream',self.wire.url,'--chain-id','1337','--genesis-hash',IDENTITY.genesis_hash,'--report',str(report)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=process.stderr.readline();self.assertIn('Listening',line)
            process.send_signal(signal.SIGTERM);out,err=process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,err);self.assertTrue(report.exists())
            body=report.read_text();self.assertNotIn(self.tmp.name,body);self.assertNotIn(self.wire.url,body)
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def test_overload_503_does_not_become_savings(self):
        d=self.daemon();port=self.server(d);server=self.servers[-1][0]
        server.capacity=1;self.wire.gate=threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(rpc,port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])
            wait_until(lambda:self.wire.counts['eth_getStorageAt']==1)
            self.assertEqual(rpc(port,'eth_getStorageAt',[ADDRESS,'0x8',PIN])[0],503)
            self.wire.gate.set();self.assertIn('result',first.result()[1])
        self.assertEqual(server.overload,1);self.assertEqual(d.metrics()['client']['success'],1)
        self.assertEqual(d.metrics()['foreground_avoidance_percent'],0)

    def test_remote_binding_requires_auth_and_no_wildcard_cors(self):
        d=self.daemon()
        with self.assertRaises(ValueError):serve(d,0,host='0.0.0.0')
        with self.assertRaises(ValueError):serve(d,0,origins=['*'])
        with self.assertRaises(ValueError):serve(d,0,origins=['https://example.org/private'])

    def test_prefetch_failure_never_replaces_authoritative_call_result(self):
        d=self.daemon(catalog=self.catalog());self.wire.fault='malformed'
        result=d.read('eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN]);wait_until(lambda:not d.prefetch.stats()['pending'])
        self.assertEqual(result,'0x'+'08'.zfill(64));self.assertEqual(d.prefetch.stats()['errors'],1)
        self.assertIsNone(d.edge.peek('eth_getStorageAt',[ADDRESS,'0x8',PIN]))

    def test_peer_bad_envelope_falls_back_and_never_poison_cache(self):
        topology=load_topology(ROOT/'data/mesh-topology.json');neighbor=topology[0][0]
        b_mesh=MeshOrigin(self.origin,neighbor,{},TOKEN,topology);b=self.daemon(mesh=b_mesh)
        port=self.server(b,peer=True,token=TOKEN)
        a_mesh=MeshOrigin(self.origin,0,{neighbor:f'http://127.0.0.1:{port}'},TOKEN,topology);a=self.daemon(mesh=a_mesh)
        with patch.object(b_mesh,'receive',return_value={'schema':'synafly.mesh-result.v1','role':neighbor,'key':'wrong','hit':True,'value':'0x'+'ff'*32}):
            result=a.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        self.assertEqual(result,'0x'+'08'.zfill(64));self.assertEqual(a_mesh.stats()['failures'],1)
        self.assertEqual(a.receipts.snapshot()['total'],0)

    def test_receipt_mutation_and_genesis_commitment_shape(self):
        d=self.daemon();d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN]);d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        row=d.receipts.snapshot()['retained'][0]
        self.assertEqual(set(row['commitment']),{'lineage','graph','model','checkpoint','parent','sequence','tick'})
        self.assertEqual(row['commitment']['parent'],ZERO);self.assertEqual(row['commitment']['sequence'],0);self.assertEqual(row['commitment']['tick'],0)
        core={k:v for k,v in row.items() if k not in {'receipt_hash','commitment'}};core['upstream_saved']=999
        self.assertNotEqual(digest(core),row['receipt_hash'])

    def test_receipt_encodes_with_existing_registry_adapter(self):
        from synafly_lab.anchor import message_calldata
        d=self.daemon();d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN]);d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        exported=d.receipts.snapshot()['retained'][0]['commitment']
        adapted={k:v[2:] if k in {'lineage','graph','model','checkpoint','parent'} else v for k,v in exported.items()}
        wire=message_calldata(adapted)
        self.assertEqual(len(bytes.fromhex(wire[2:])),4+7*32)
        self.assertEqual(wire[-128:],'0'*128)

    def test_ten_thousand_reuses_keep_bounded_containers(self):
        d=self.daemon(max_cache=2,receipt_capacity=8)
        for _ in range(10000):d.read('eth_getStorageAt',[ADDRESS,'0x8',PIN])
        self.assertEqual(d.edge.stats()['cache_entries'],1)
        self.assertEqual(d.receipts.snapshot()['total'],9999)
        self.assertEqual(len(d.receipts.snapshot()['retained']),8)
        self.assertEqual(self.wire.counts['eth_getStorageAt'],1)
        self.assertEqual(d.edge.stats()['inflight_keys'],0)

    def test_no_catalog_means_no_hidden_prefetch_or_extra_reads(self):
        d=self.daemon();d.read('eth_call',[{'to':ADDRESS,'data':SELECTOR},PIN])
        self.assertEqual(self.wire.counts['eth_call'],1);self.assertEqual(self.wire.counts['eth_getStorageAt'],0)
        self.assertFalse(d.prefetch.stats()['enabled']);self.assertEqual(d.metrics()['net_rpc_reduction_percent'],0)


class KeccakTests(unittest.TestCase):
    def test_all_305_recorded_vectors_without_subprocess(self):
        corpus=json.loads((ROOT/'data/evm-access-recipes.json').read_text())
        with patch('subprocess.run',side_effect=AssertionError('No external hash implementation')):
            for row in corpus['keccak_wire_fixtures']:
                self.assertEqual('0x'+keccak256(bytes.fromhex(row['input'][2:])).hex(),row['output'])
        self.assertNotEqual(keccak256(b''),hashlib.sha3_256(b'').digest())
    def test_keccak_rate_boundaries_against_recorded_cast_vectors(self):
        vectors={135:'cbdfd9dee5faad3818d6b06f95a219fd290b0e1706f6a82e5a595b9ce9faca62',
                 136:'7ce759f1ab7f9ce437719970c26b0a66ff11fe3e38e17df89cf5d29c7d7f807e',
                 137:'ac73d4fae68b8453f764007c1a20ce95994187861f0c3227a3a8e99a73a3b1db',
                 271:'7c974895b2a88303ff2dc6b58f438ceb0b298cac91099ac0539cc0f477506191',
                 272:'fdf2ec49e749960d3c8521a0219af8d03e30e2b3bf19bd16150ee0eaf133d66e',
                 4096:'1c85a3e5666494583f321cd54285cc17276acf9aea34b207d43005bfa69d0a86'}
        for length,expected in vectors.items():
            self.assertEqual(keccak256(bytes(i%256 for i in range(length))).hex(),expected)

    def test_runtime_flyhash_matches_all_frozen_pr7_predictions(self):
        from synafly_lab.runtime_recipes import RuntimeRecipeIndex
        from synafly_lab.access_recipes import call_context,footprint
        corpus=json.loads((ROOT/'data/evm-access-recipes.json').read_text());index=RuntimeRecipeIndex()
        def context(row):return call_context(bytes.fromhex(row['data'][2:]),int(row['caller'],16))
        for parsed in corpus['parsed_recipes']:
            row=corpus['training'][parsed['training_row']]
            index.add(row['code_hash'],row['data'][2:10],context(row),parsed['recipe'],footprint(row['trace']))
        index.frozen=True
        self.assertEqual(index.fingerprint(),corpus['catalog_sha256'])
        with patch('subprocess.run',side_effect=AssertionError('No cast')):
            for prediction in corpus['frozen_predictions']:
                row=corpus['heldout_inputs'][prediction['query']]
                actual=index.query(prediction['method'],row['code_hash'],row['data'][2:10],context(row))
                self.assertEqual(actual,{k:v for k,v in prediction.items() if k not in {'query','method'}})

    def test_topology_export_exactly_matches_pr5(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from benchmark_swarm_topology import build_networks,validate_inputs
        report=json.loads((ROOT/'results/swarm-topology-benchmark.json').read_text())
        nets,_=build_networks(validate_inputs(report['inputs']))
        self.assertEqual(load_topology(ROOT/'data/mesh-topology.json'),nets['affinity'].adjacent)

if __name__=='__main__':unittest.main()
