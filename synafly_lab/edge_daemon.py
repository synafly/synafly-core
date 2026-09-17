"""Runnable, bounded read-only RPC edge with opt-in recipe prefetch and trusted mesh."""
import argparse
from collections import Counter
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import resource
import sys
import time
import signal
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from .runtime_edge import RuntimeReadEdge
from .edge_mesh import MeshOrigin, load_topology, TOPOLOGY_SHA256
from .offload_receipts import ReceiptLog
from .recipe_prefetch import RecipePrefetcher, load_catalog
from .rpc import (BSC_GENESIS, HttpOrigin, NetworkIdentity, RpcError, block_ref,
                  encode, hex_data, load_json, parse_read, quantity)

LOG = logging.getLogger('synafly.edge')
METHODS = frozenset({'eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber',
                     'eth_getCode', 'eth_getStorageAt', 'eth_call'})
DEFAULT_UPSTREAM = 'https://bsc-dataseed.bnbchain.org'


class DaemonOrigin(HttpOrigin):
    def __init__(self, *args, **kwargs):
        self.local = threading.local(); self.lane_counts = Counter(); self.lane_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    @contextmanager
    def lane(self, name):
        old = getattr(self.local, 'lane', 'bootstrap'); self.local.lane = name
        try: yield
        finally: self.local.lane = old

    def call(self, method, params):
        if method not in METHODS: raise RpcError(-32601, 'Read-only method whitelist')
        with self.lane_lock:
            lane = getattr(self.local, 'lane', 'bootstrap')
            self.lane_counts[lane] += 1
            self.lane_counts[lane + ':' + method] += 1
        value = self.extra_call(method, params) if method in {'eth_call','eth_blockNumber'} else super().call(method, params)
        try:
            if method in {'eth_chainId','eth_blockNumber'}: return quantity(value, 64)
            if method == 'eth_call': return hex_data(value)
            if method == 'eth_getBlockByNumber':
                if value is None: return None
                if type(value) is not dict: raise ValueError('Block object')
                # Pending headers may have null number/hash; they are never cacheable.
                if value.get('number') is not None: quantity(value['number'], 64)
                if value.get('hash') is not None: hex_data(value['hash'], 32)
                if 'number' not in value or 'hash' not in value: raise ValueError('Block identity')
            return value
        except (ValueError, TypeError) as exc: raise RpcError(-32002, 'Malformed upstream result') from exc

    def extra_call(self, method, params):
        # Opt-in new read methods without changing PR #4's locked transport policy.
        with self._lock:
            request_id = next(self._ids)
            raw = encode({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
            self._counts['rpc_calls'] += 1
            self._counts['metadata_calls' if method == 'eth_blockNumber' else 'state_reads'] += 1
            self._counts['request_json_bytes'] += len(raw)
        try: value = load_json(self._exchange(raw))
        except RpcError as exc: raise RpcError(-32002,'Malformed upstream reply') from exc
        if type(value) is not dict or value.get('jsonrpc') != '2.0' or type(value.get('id')) is not int or value['id'] != request_id or ('result' in value) == ('error' in value):
            raise RpcError(-32002,'Upstream response identity mismatch')
        if 'error' in value:
            error = value['error']
            if type(error) is not dict or type(error.get('code')) is not int or not -(2**31) <= error['code'] < 2**31 or type(error.get('message')) is not str:
                raise RpcError(-32002,'Malformed upstream error')
            raise RpcError(error['code'],'Upstream rejected the read')
        return value['result']

    def metrics(self):
        with self.lane_lock: lanes = dict(self.lane_counts)
        return {**self.counters(), 'lanes': lanes}


def parse_call(params):
    if type(params) is not list or len(params) not in (1, 2) or type(params[0]) is not dict:
        raise RpcError(-32602, 'eth_call parameters')
    call = dict(params[0])
    if set(call) - {'to','from','data','input','value','gas','gasPrice','maxFeePerGas','maxPriorityFeePerGas'} or 'to' not in call:
        raise RpcError(-32602, 'Unsupported call profile; no overrides or contract creation')
    for key in ('to','from'):
        if key in call: call[key] = hex_data(call[key], 20)
    for key in ('data','input'):
        if key in call: call[key] = hex_data(call[key], maximum=4096)
    if 'input' in call and 'data' in call:
        raise RpcError(-32602, 'Use either input or data')
    for key in ('value','gas','gasPrice','maxFeePerGas','maxPriorityFeePerGas'):
        if key in call: call[key] = quantity(call[key])
    if 'gasPrice' in call and any(k in call for k in ('maxFeePerGas','maxPriorityFeePerGas')):
        raise RpcError(-32602, 'Conflicting fee fields')
    if 'gas' in call and int(call['gas'], 16) > 5_000_000:
        raise RpcError(-32602, 'Call gas exceeds local policy')
    # Omitted gas is forwarded without rewriting its meaning; upstream must set a gas cap.
    return call, block_ref(params[1] if len(params) == 2 else 'latest')


class EdgeDaemon:
    def __init__(self, upstream, *, max_cache=1024, cache_bytes=4*1024*1024, ttl=60,
                 catalog=None, prefetch_budget=60, mesh=None, receipt_capacity=1024):
        self.origin = upstream; self.identity = upstream.identity; self.mesh = mesh
        self.started = time.monotonic()
        self.edge = RuntimeReadEdge(mesh or upstream, cache_entries=max_cache, cache_bytes=cache_bytes, ttl=ttl, max_inflight=16)
        if mesh: mesh.edge = self.edge
        self.prefetch = RecipePrefetcher(self.edge, catalog, budget=prefetch_budget)
        self.receipts = ReceiptLog('0x'+TOPOLOGY_SHA256 if mesh else '0x'+'01'*32, receipt_capacity)
        self.lock = threading.Lock(); self.counts = Counter()

    def read(self, method, params):
        with self.lock: self.counts['ingress'] += 1; self.counts['active'] += 1
        try:
            if type(method) is not str or method not in METHODS: raise RpcError(-32601, 'Read-only method whitelist')
            if type(params) is not list: raise RpcError(-32602, 'Positional parameters required')
            with self.origin.lane('foreground'):
                if method in {'eth_getStorageAt','eth_getCode'}:
                    request = parse_read(method, params)
                    value, source = self.edge.read(method, params, with_source=True)
                    if source == 'origin' and self.mesh and getattr(self.mesh.local, 'peer_hit', False): source = 'peer'
                    if source != 'origin':
                        with self.lock: self.counts['reuse_' + source] += 1
                        self.receipts.append(request, source)
                elif method == 'eth_call':
                    call, block = parse_call(params)
                    value = self.origin.call(method, [call, block.wire()])
                    self.prefetch.schedule(call, block)
                elif method in {'eth_chainId','eth_blockNumber'}:
                    if params: raise RpcError(-32602, 'Expected no parameters')
                    value = self.origin.call(method, [])
                else:
                    if len(params) != 2 or type(params[1]) is not bool or type(params[0]) is not str:
                        raise RpcError(-32602, 'Block query parameters')
                    block = block_ref(params[0]); value = self.origin.call(method, [block.wire(), params[1]])
            with self.lock: self.counts['success'] += 1
            return value
        except RpcError as exc:
            with self.lock:
                self.counts['errors'] += 1
                self.counts['rejected' if exc.code in {-32601,-32602} else 'failed'] += 1
            raise
        finally:
            with self.lock: self.counts['active'] -= 1

    def metrics(self):
        with self.lock: client = dict(self.counts)
        upstream = self.origin.metrics(); lanes = upstream['lanes']
        prefetch = self.prefetch.stats()
        complete = not client.get('active') and not client.get('errors') and not prefetch['pending']
        demand = client.get('success', 0); foreground = lanes.get('foreground', 0)
        extra = lanes.get('prefetch', 0) + lanes.get('watcher', 0)
        return {'schema':'synafly.edge-daemon-metrics.v1', 'client':client, 'cache':self.edge.stats(),
                'upstream':upstream, 'prefetch':prefetch, 'mesh': self.mesh.stats() if self.mesh else None,
                'receipts_total':self.receipts.count,
                'foreground_avoidance_percent':round(100*(1-foreground/demand),6) if complete and demand else None,
                'net_rpc_reduction_percent':round(100*(1-(foreground+extra)/demand),6) if complete and demand else None,
                'resources':self.resources(),
                'cost_scope':'RPC counts, not measured NVMe, money, consensus or biological superiority'}

    def resources(self):
        usage=resource.getrusage(resource.RUSAGE_SELF)
        return {'process_peak_rss_bytes':int(usage.ru_maxrss*(1 if sys.platform=='darwin' else 1024)),
                'cpu_user_seconds':usage.ru_utime,'cpu_system_seconds':usage.ru_stime,
                'uptime_seconds':round(time.monotonic()-self.started,6)}

    def close(self): self.prefetch.close()


class DaemonHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 128
    def __init__(self, *args, capacity=32, **kwargs):
        self.slots = threading.BoundedSemaphore(capacity)
        self.metric_lock = threading.Lock(); self.overload = 0
        super().__init__(*args, **kwargs)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            with self.metric_lock: self.overload += 1
            try:
                request.settimeout(.1)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError: pass
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request, address)
        except BaseException: self.slots.release(); raise
    def process_request_thread(self, *args):
        try: super().process_request_thread(*args)
        finally: self.slots.release()


def serve(daemon, port=8545, **kwargs):
    from .daemon_ingress import AsyncDaemonServer
    return AsyncDaemonServer(daemon,port,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream',default=DEFAULT_UPSTREAM)
    parser.add_argument('--port',type=int,default=8545)
    parser.add_argument('--host',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1')
    parser.add_argument('--chain-id',type=int,default=56); parser.add_argument('--genesis-hash',default=BSC_GENESIS)
    parser.add_argument('--capacity',type=int,default=1024)
    parser.add_argument('--max-cache',type=int,default=1024); parser.add_argument('--cache-bytes',type=int,default=4*1024*1024)
    parser.add_argument('--recipe-catalog'); parser.add_argument('--prefetch-budget',type=int,default=60)
    parser.add_argument('--peer-port',type=int); parser.add_argument('--role',type=int,default=0)
    parser.add_argument('--peers',nargs='*',default=[],metavar='ROLE=ORIGIN')
    parser.add_argument('--allow-origin',action='append',default=[]); parser.add_argument('--allowed-host',action='append',default=[])
    parser.add_argument('--watch-blocks',action='store_true'); parser.add_argument('--watch-interval',type=float,default=12)
    parser.add_argument('--report',default='.local/edge-daemon-report.json')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='[SynaFly Edge] %(message)s')
    for port in (args.port,args.peer_port):
        if port is not None and not 0 <= port <= 65535: parser.error('Port bound')
    if args.peer_port is None and args.peers: parser.error('--peers requires --peer-port')
    if args.watch_blocks and not args.recipe_catalog: parser.error('--watch-blocks requires a reviewed --recipe-catalog')
    upstream=DaemonOrigin(args.upstream,NetworkIdentity(args.chain_id,args.genesis_hash))
    catalog=load_catalog(args.recipe_catalog,upstream.identity) if args.recipe_catalog else None
    mesh=None
    if args.peer_port is not None:
        peers={}
        for entry in args.peers:
            role,sep,url=entry.partition('=')
            if not sep or not role.isdigit() or int(role) in peers: parser.error('Use unique ROLE=ORIGIN peers')
            peers[int(role)]=url
        topology=load_topology(Path(__file__).resolve().parents[1]/'data/mesh-topology.json')
        mesh=MeshOrigin(upstream,args.role,peers,os.getenv('SYNAFLY_MESH_TOKEN',''),topology)
    daemon=EdgeDaemon(upstream,max_cache=args.max_cache,cache_bytes=args.cache_bytes,catalog=catalog,prefetch_budget=args.prefetch_budget,mesh=mesh)
    servers=[]; threads=[]; stop=threading.Event(); watcher=None
    def shutdown_signal(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,shutdown_signal)
    try:
        rpc=serve(daemon,args.port,host=args.host,token=os.getenv('SYNAFLY_RPC_TOKEN',''),origins=args.allow_origin,allowed_hosts=args.allowed_host,capacity=args.capacity)
        servers.append(rpc)
        if mesh: servers.append(serve(daemon,args.peer_port,host=args.host,peer=True,token=mesh.token,allowed_hosts=args.allowed_host))
        for server in servers:
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1});thread.start();threads.append(thread)
            if not server.ready.wait(5) or server.finished.is_set(): raise RuntimeError('Ingress startup failed')
        if args.watch_blocks:
            from .block_warmer import BlockWarmer
            watcher=BlockWarmer(daemon,args.watch_interval);watcher.start()
        LOG.info('Listening on http://%s:%d -> Upstream: %s',args.host,rpc.server_port,urlsplit(args.upstream).hostname)
        while not stop.wait(5):
            metrics=daemon.metrics();LOG.info('Stats %s',json.dumps(metrics,sort_keys=True))
    except KeyboardInterrupt: LOG.info('Draining admitted requests and bounded prefetch work')
    finally:
        if watcher: watcher.close()
        for server in servers[:len(threads)]: server.shutdown()
        for server in servers: server.server_close()
        for thread in threads: thread.join()
        daemon.close()
        report={**daemon.metrics(),'http_overload_rejections':sum(s.overload for s in servers),'peak_admitted':max((s.peak_admitted for s in servers),default=0),'transport_errors':sum(s.transport_errors for s in servers),'receipts':daemon.receipts.snapshot()}
        if watcher: report['watcher']=watcher.stats()
        path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(encode(report)+b'\n');temporary.replace(path)
        LOG.info('Shutdown report written (operator-selected local path)')

if __name__=='__main__': main()
