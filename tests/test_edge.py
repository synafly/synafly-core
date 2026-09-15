import concurrent.futures
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from synafly_lab.edge import ReadEdge
from synafly_lab.edge_server import dispatch, serve
from synafly_lab.rpc import (BSC_GENESIS, HttpOrigin, NetworkIdentity, RpcError,
                            block_ref, cache_key, encode, load_json, parse_read)
from rpc_fixture import (ADDRESS, BLOCK_A, BLOCK_B, BLOCK_MISSING, GENESIS,
                         IDENTITY, OriginFixture, state_value)

def params(block=BLOCK_A, canonical=False):
    return [ADDRESS, {'blockHash': block, 'requireCanonical': canonical}]

class WireTests(unittest.TestCase):
    def test_supported_read_normalization_and_explicit_scope(self):
        read = parse_read('eth_getBalance', [ADDRESS.upper().replace('0X','0x'), {'blockHash': BLOCK_A.upper().replace('0X','0x')}])
        self.assertEqual(read.wire_params(), params())
        self.assertTrue(read.block.cacheable)
        for method in ['eth_sendRawTransaction', 'eth_call', 'admin_peers', 'debug_traceTransaction', 'eth_getLogs']:
            with self.assertRaises(RpcError): parse_read(method, [])
    def test_state_selector_cache_boundary(self):
        for value in ['latest', 'pending', 'safe', 'finalized', 'earliest', '0x2', {'blockNumber':'0x2'}, {'blockHash':BLOCK_A,'requireCanonical':True}]:
            self.assertFalse(block_ref(value).cacheable)
        for value in [True, 3, '0x00', {}, {'blockHash':BLOCK_A,'blockNumber':'0x2'}, {'blockHash':BLOCK_A,'requireCanonical':1}, {'blockNumber':'0x2','requireCanonical':False}]:
            with self.assertRaises(RpcError): block_ref(value)
    def test_keys_bind_network_method_arguments_and_context(self):
        req = parse_read('eth_getBalance', params())
        base = cache_key(IDENTITY, req)
        others = [cache_key(NetworkIdentity(56, BSC_GENESIS), req),
            cache_key(NetworkIdentity(1337, BLOCK_B), req),
            cache_key(IDENTITY, parse_read('eth_getCode', params())),
            cache_key(IDENTITY, parse_read('eth_getBalance', params(BLOCK_B))),
            cache_key(IDENTITY, parse_read('eth_getBalance', params(canonical=True))),
            cache_key(IDENTITY, parse_read('eth_getBalance', ['0x'+'cd'*20, params()[1]]))]
        self.assertTrue(all(key != base for key in others))
    def test_duplicate_depth_nonfinite_and_large_wire_data(self):
        for raw in [b'{"id":1,"id":2}', b'{"n":NaN}', b'['*20+b'0'+b']'*20, b'1'*5000, b'\xff']:
            with self.assertRaises(RpcError): load_json(raw)
        with self.assertRaises(RpcError): load_json(b' '*20, 10)
        self.assertEqual(load_json(b'{"result":null,"id":true}'), {'result':None,'id':True})
    def test_parameters_are_typed_and_bounded(self):
        for value in [[], [True], ['0x01'], [ADDRESS, BLOCK_A], [ADDRESS, None], [ADDRESS,'latest','extra']]:
            with self.assertRaises(RpcError): parse_read('eth_getBalance', value)
        with self.assertRaises(RpcError): parse_read('eth_getStorageAt', [ADDRESS,'0x'+'f'*65,'latest'])
        with self.assertRaises(RpcError): NetworkIdentity(True, GENESIS)

class EdgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = OriginFixture().__enter__()
        self.origin = HttpOrigin(self.fixture.url, IDENTITY, timeout=0.5)
        self.edge = ReadEdge(self.origin)
    def tearDown(self): self.fixture.__exit__(None,None,None)
    def test_bootstrap_identity_and_write_denial(self):
        self.assertEqual(self.origin.counters()['metadata_calls'], 2)
        with self.assertRaises(RpcError): self.origin.call('eth_sendRawTransaction', ['0x00'])
        self.assertEqual(self.origin.counters()['rpc_calls'], 2)
        with self.assertRaises(RpcError): HttpOrigin(self.fixture.url, NetworkIdentity(56, GENESIS))
        self.fixture.fault = 'genesis'
        with self.assertRaises(RpcError): HttpOrigin(self.fixture.url, IDENTITY)
    def test_repeat_pinned_read_reuses_value_not_request_id(self):
        a = dispatch(self.edge, {'jsonrpc':'2.0','id':'alpha','method':'eth_getBalance','params':params()})
        b = dispatch(self.edge, {'jsonrpc':'2.0','id':42,'method':'eth_getBalance','params':params()})
        self.assertEqual(a['result'], b['result'])
        self.assertEqual(a['id'], 'alpha');self.assertEqual(b['id'], 42)
        self.assertEqual(self.fixture.reads, 1)
        self.assertEqual(self.edge.stats()['cache_hits'], 1)
    def test_new_block_never_reuses_old_block_state(self):
        a = self.edge.read('eth_getBalance', params())
        b = self.edge.read('eth_getBalance', params(BLOCK_B))
        self.assertNotEqual(a,b);self.assertEqual(self.fixture.reads,2)
    def test_latest_number_and_canonical_requests_always_forward(self):
        for selector in ['latest','pending','safe','finalized','0x2', {'blockHash':BLOCK_A,'requireCanonical':True}]:
            for _ in range(2): self.edge.read('eth_getBalance', [ADDRESS, selector])
        self.assertEqual(self.fixture.reads,12)
        self.assertEqual(self.edge.stats()['cache_entries'],0)
    def test_reorg_does_not_reuse_cached_orphan_as_canonical(self):
        pinned = self.edge.read('eth_getBalance', params())
        latest_a = self.edge.read('eth_getBalance',[ADDRESS,'latest'])
        self.fixture.head = BLOCK_B
        latest_b = self.edge.read('eth_getBalance',[ADDRESS,'latest'])
        self.assertNotEqual(latest_a, latest_b)
        self.assertEqual(self.edge.read('eth_getBalance', params()), pinned)
        for _ in range(2):
            with self.assertRaises(RpcError) as error: self.edge.read('eth_getBalance', params(canonical=True))
            self.assertEqual(error.exception.code, -32000)
        self.assertEqual(self.fixture.reads,5)
    def test_unknown_block_and_upstream_errors_are_not_cached(self):
        for _ in range(2):
            with self.assertRaises(RpcError): self.edge.read('eth_getBalance', params(BLOCK_MISSING))
        self.fixture.fault = 'error'
        with self.assertRaises(RpcError): self.edge.read('eth_getBalance', params())
        self.fixture.fault = None
        self.edge.read('eth_getBalance', params())
        self.assertEqual(self.fixture.reads,4)
    def test_bad_frames_do_not_poison_cache(self):
        for fault in ['id','bool-id','both','result','null','error-code','duplicate','oversize','redirect','content-type']:
            with self.subTest(fault=fault):
                self.fixture.fault = fault
                with self.assertRaises(RpcError) as error: self.edge.read('eth_getBalance', params())
                self.assertEqual(error.exception.code,-32002)
                self.assertEqual(self.edge.stats()['cache_entries'],0)
        self.fixture.fault = None
        self.edge.read('eth_getBalance', params())
        self.assertEqual(self.edge.stats()['cache_entries'],1)
    def test_cache_expiry_lru_and_byte_accounting(self):
        clock = [0.0]
        edge = ReadEdge(self.origin, cache_entries=1, cache_bytes=12, ttl=2, clock=lambda:clock[0])
        a=edge.read('eth_getBalance',params());self.assertEqual(edge.stats()['cache_value_bytes'],len(a))
        clock[0]=3;edge.read('eth_getBalance',params());self.assertEqual(self.fixture.reads,2)
        edge.read('eth_getBalance',params(BLOCK_B));self.assertEqual(edge.stats()['cache_entries'],1)
        edge.read('eth_getCode',params());self.assertEqual(edge.stats()['cache_entries'],1)
        self.assertLessEqual(edge.stats()['cache_value_bytes'],12)
    def test_disabled_cache_does_not_retain_values(self):
        edge=ReadEdge(self.origin,cache_entries=0)
        for _ in range(3):edge.read('eth_getBalance',params())
        self.assertEqual(self.fixture.reads,3);self.assertEqual(edge.stats()['cache_entries'],0)
    def test_single_flight_concurrency_and_cleanup(self):
        self.fixture.pause()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs=[pool.submit(self.edge.read,'eth_getBalance',params()) for _ in range(8)]
            self.fixture.wait_reads(1)
            deadline=time.monotonic()+1
            while self.edge.stats()['coalesced_waiters']<7 and time.monotonic()<deadline:time.sleep(.001)
            self.assertEqual(self.edge.stats()['coalesced_waiters'],7)
            self.fixture.release()
            self.assertEqual(len({job.result(timeout=2) for job in jobs}),1)
        self.assertEqual(self.fixture.reads,1);self.assertEqual(self.edge.stats()['inflight_keys'],0)
    def test_cache_only_concurrent_store_accounting(self):
        edge=ReadEdge(self.origin,coalesce=False)
        self.fixture.pause()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(edge.read,'eth_getBalance',params()) for _ in range(4)]
            self.fixture.wait_reads(4);self.fixture.release()
            values=[job.result(timeout=2) for job in jobs]
        self.assertEqual(edge.stats()['cache_value_bytes'],len(values[0]))
        self.assertEqual(edge.stats()['cache_entries'],1)
    def test_capacity_rejects_distinct_work_and_recovers(self):
        edge=ReadEdge(self.origin,max_inflight=1)
        self.fixture.pause()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(edge.read,'eth_getBalance',params())
            self.fixture.wait_reads(1)
            with self.assertRaises(RpcError) as error:edge.read('eth_getBalance',params(BLOCK_B))
            self.assertEqual(error.exception.code,-32005)
            self.assertEqual(edge.stats()['inflight_keys'],1)
            self.fixture.release();job.result(timeout=2)
        edge.read('eth_getBalance',params(BLOCK_B))
        self.assertEqual(edge.stats()['inflight_keys'],0)
    def test_timeout_clears_flight_and_allows_retry(self):
        self.fixture.pause()
        with self.assertRaises(RpcError):self.edge.read('eth_getBalance',params())
        self.assertEqual(self.edge.stats()['inflight_keys'],0)
        self.fixture.release();self.edge.read('eth_getBalance',params())
    def test_trickling_headers_and_bodies_do_not_extend_deadline(self):
        for fault in ['slow-header','slow-body']:
            self.fixture.fault=fault;start=time.monotonic()
            with self.assertRaises(RpcError):self.edge.read('eth_getBalance',params())
            self.assertLess(time.monotonic()-start,2)
            self.assertEqual(self.edge.stats()['inflight_keys'],0)
        self.fixture.fault=None;self.edge.read('eth_getBalance',params())
    def test_coalesced_failure_wakes_every_follower_and_retry_succeeds(self):
        self.fixture.pause();self.fixture.fault='error'
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(self.edge.read,'eth_getBalance',params()) for _ in range(4)]
            self.fixture.wait_reads(1);deadline=time.monotonic()+1
            while self.edge.stats()['coalesced_waiters']<3 and time.monotonic()<deadline:time.sleep(.001)
            self.assertEqual(self.edge.stats()['coalesced_waiters'],3);self.fixture.release()
            for job in jobs:
                with self.assertRaises(RpcError):job.result(timeout=2)
        self.assertEqual(self.fixture.reads,1);self.assertEqual(self.edge.stats()['inflight_keys'],0)
        self.fixture.fault=None;self.edge.read('eth_getBalance',params())
        self.assertEqual(self.fixture.reads,2)
    def test_notifications_batches_and_id_validation(self):
        request={'jsonrpc':'2.0','method':'eth_getBalance','params':params()}
        self.assertIsNone(dispatch(self.edge,request))
        result=dispatch(self.edge,[request,{**request,'id':'ok'},{**request,'id':False}])
        self.assertEqual(len(result),2);self.assertEqual(result[0]['id'],'ok')
        self.assertEqual(result[1]['error']['code'],-32600)
        for value in [[],[request]*9,{'jsonrpc':'1.0'}, {**request,'id':1.5}]:
            self.assertIn('error',dispatch(self.edge,value))
    def test_http_surface_and_browser_boundary(self):
        server=serve(self.edge);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def post(raw,headers=None,path='/rpc'):
            r=urllib.request.Request(base+path,data=raw,headers=headers or {'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(r,timeout=2) as response:return response.status,response.read()
            except urllib.error.HTTPError as error:
                try:return error.code,error.read()
                finally:error.close()
        try:
            request=encode({'jsonrpc':'2.0','id':7,'method':'eth_getBalance','params':params()})
            status,raw=post(request);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['id'],7)
            self.assertEqual(post(request,{'Content-Type':'application/json','Origin':'https://example.com'})[0],403)
            self.assertEqual(post(request,{'Content-Type':'application/json','Host':'evil.example'})[0],403)
            self.assertEqual(post(request,{'Content-Type':'text/plain'})[0],415)
            self.assertEqual(post(b' '*16385)[0],413)
            self.assertEqual(post(request,path='/admin')[0],404)
            self.assertEqual(json.loads(post(b'{"id":1,"id":2}')[1])['error']['code'],-32700)
            notification=encode({'jsonrpc':'2.0','method':'eth_getBalance','params':params()})
            self.assertEqual(post(notification),(204,b''))
            with urllib.request.urlopen(base+'/metrics') as response:metrics=json.load(response)
            self.assertIn('cache_hits',metrics['edge'])
            self.assertNotIn(ADDRESS,json.dumps(metrics))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
