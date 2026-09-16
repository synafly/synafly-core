#!/usr/bin/env python3
"""1,000 real concurrent loopback HTTP arrivals; no public load generation."""
import argparse
import asyncio
from collections import Counter
from contextlib import AbstractContextManager
from functools import partial
import hashlib
import http.client
import json
import math
from pathlib import Path
import resource
import socket
import subprocess
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synafly_lab.async_edge_ingress import AsyncIngress
from synafly_lab.edge import ReadEdge
from synafly_lab.edge_server import dispatch
from synafly_lab.rpc import HttpOrigin, NetworkIdentity, RpcError, encode, load_json, parse_read
from synafly_lab.tier_cost import score

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['scripts/benchmark_tier_offload.py', 'synafly_lab/async_edge_ingress.py',
    'synafly_lab/edge.py', 'synafly_lab/edge_server.py', 'synafly_lab/rpc.py',
    'synafly_lab/tier_cost.py', 'docs/tiered-load-test-design.md']


def address(index):
    return '0x' + f'{0x10000 + index:040x}'


class LocalEvmOrigin(AbstractContextManager):
    """Minimal owned zero-account Anvil fixture; no historical research imports."""
    def __init__(self):
        self.process = None
        self.successful_setup_calls = Counter()
        self.readiness_successes = 0

    def _rpc(self, method, params, readiness=False):
        if method not in {'eth_chainId', 'eth_getBlockByNumber', 'anvil_setBalance', 'evm_mine'}:
            raise ValueError('Local setup method not allowed')
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            c.request('POST', '/', encode({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}),
                      {'Content-Type': 'application/json'})
            response = c.getresponse(); value = load_json(response.read(262145))
            if response.status != 200 or type(value) is not dict or value.get('id') != 1 or 'error' in value:
                raise ValueError('Local fixture setup failed')
            if readiness: self.readiness_successes += 1
            else: self.successful_setup_calls[method] += 1
            return value['result']
        finally: c.close()

    def __enter__(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.url = 'http://127.0.0.1:' + str(self.port)
        self.process = subprocess.Popen(['anvil', '--host', '127.0.0.1', '--port', str(self.port),
            '--chain-id', '1337', '--accounts', '0', '--no-mining', '--timestamp', '1735689600'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until = time.monotonic() + 8
            while True:
                if self.process.poll() is not None: raise RuntimeError('Owned local node exited')
                try:
                    if self._rpc('eth_chainId', [], readiness=True) != '0x539': raise ValueError('Local chain ID')
                    break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= until: raise RuntimeError('Owned local node readiness timeout')
                    time.sleep(.05)
            genesis = self._rpc('eth_getBlockByNumber', ['0x0', False])
            self.identity = NetworkIdentity(1337, genesis['hash'])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def reader(self):
        return HttpOrigin(self.url, self.identity, timeout=5)

    def __exit__(self, *args):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()


class DelayedOrigin:
    def __init__(self, client):
        self.client = client; self.identity = client.identity; self.timeout = client.timeout + .02
        self.lock = threading.Lock(); self.active = self.peak = 0
    def call(self, method, params):
        with self.lock: self.active += 1; self.peak = max(self.peak, self.active)
        try:
            time.sleep(.02)  # Declared emulated latency, not measured BSC/NVMe time.
            return self.client.call(method, params)
        finally:
            with self.lock: self.active -= 1


class RelayOrigin:
    def __init__(self, port, identity, direct):
        self.port = port; self.identity = identity; self.timeout = 8
        self.direct = direct
        self.lock = threading.Lock(); self.counts = Counter(); self.sequence = 0
    def call(self, method, params):
        if not parse_read(method, params).block.cacheable:
            with self.lock: self.counts['direct_selector_bypasses'] += 1
            return self.direct.call(method, params)
        with self.lock: self.sequence += 1; sequence = self.sequence
        body = encode({'jsonrpc': '2.0', 'id': sequence, 'method': method, 'params': params})
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            c.request('POST', '/rpc', body, {'Content-Type': 'application/json'})
            response = c.getresponse(); raw = response.read(262145); value = load_json(raw)
            with self.lock:
                self.counts['calls'] += 1; self.counts['request_json_bytes'] += len(body); self.counts['response_json_bytes'] += len(raw)
            if response.status != 200 or value.get('id') != sequence: raise RpcError(-32002, 'Peer transport/identity')
            if 'error' in value: raise RpcError(value['error']['code'], value['error']['message'])
            return value['result']
        finally: c.close()


async def client(port, group, request, expected):
    start = time.monotonic(); writer = None
    try:
        async with asyncio.timeout(30):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            body = encode(request)
            head = f'POST /rpc/{group} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()
            writer.write(head + body); await writer.drain()
            header = (await reader.readuntil(b'\r\n\r\n')).decode('ascii').split('\r\n')
            status = int(header[0].split(' ')[1]); fields = dict(line.split(':', 1) for line in header[1:] if line)
            length = int(fields['Content-Length'])
            if not 0 <= length <= 262144: raise ValueError('Response bound')
            response = load_json(await reader.readexactly(length))
            correct = status == 200 and response.get('id') == request['id'] and response.get('result') == expected
            return {'status': status, 'correct': correct, 'rpc_error': response.get('error'),
                    'latency_ms': (time.monotonic() - start) * 1000}
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        return {'status': None, 'correct': False, 'transport_error': type(exc).__name__, 'latency_ms': (time.monotonic() - start) * 1000}
    finally:
        if writer is not None:
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass


async def run_case(node, mode, workload, n=1000, capacity=1024):
    readers = []; origin_wrappers = []; relays = []; edges = []; coordinator = None; center = None
    gate = asyncio.Event(); frontend = None; tasks = []
    try:
        if mode == 'shared':
            reader = node.reader(); readers.append(reader); wrapped = DelayedOrigin(reader); origin_wrappers.append(wrapped)
            center = ReadEdge(wrapped, max_inflight=16)
            coordinator = await AsyncIngress({'/rpc': partial(dispatch, center)}, capacity=1024).start()
        for group in range(4):
            if mode == 'shared':
                origin = RelayOrigin(coordinator.port, node.identity, wrapped); relays.append(origin)
            else:
                reader = node.reader(); readers.append(reader); origin = DelayedOrigin(reader); origin_wrappers.append(origin)
            edges.append(ReadEdge(origin, cache_entries=0 if mode == 'pass-through' else 128,
                                  coalesce=mode != 'pass-through', max_inflight=16))
        frontend = await AsyncIngress({f'/rpc/{g}': partial(dispatch, e) for g, e in enumerate(edges)}, capacity=capacity, gate=gate).start()
        for i in range(n):
            group = i % 4
            key = 0 if workload in {'hot-1', 'latest'} else (i // 4) % 8 if workload == 'hot-8' else i
            selector = 'latest' if workload == 'latest' else node.block
            query = {'jsonrpc': '2.0', 'id': i, 'method': 'eth_getBalance', 'params': [address(key), selector]}
            tasks.append(asyncio.create_task(client(frontend.port, group, query, hex(1000 + key))))
        deadline = time.monotonic() + 12
        while frontend.counts['received_requests'] < n:
            if time.monotonic() > deadline: raise RuntimeError('Not all concurrent requests reached ingress')
            await asyncio.sleep(.002)
        active_at_release = frontend.active
        start = time.monotonic(); gate.set(); outcomes = await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start
        successful = sum(x['correct'] for x in outcomes); rejected = sum(x['status'] == 503 for x in outcomes)
        transport_errors = sum(x['status'] is None for x in outcomes)
        wrong_or_rpc_errors = n - successful - rejected - transport_errors
        origin = Counter()
        for r in readers: origin.update(r.counters())
        peer = Counter()
        for relay in relays: peer.update(relay.counts)
        peer_bytes = peer['request_json_bytes'] + peer['response_json_bytes']
        if coordinator is not None:
            if peer['calls'] != coordinator.counts['received_requests']: raise ValueError('Peer request accounting')
            if peer_bytes != coordinator.counts['request_body_bytes'] + coordinator.counts['response_body_bytes']:
                raise ValueError('Peer JSON accounting')
        latencies = sorted(x['latency_ms'] for x in outcomes)
        percentile = lambda p: round(latencies[max(0, math.ceil(p * n) - 1)], 3)
        eligible = successful == n and not transport_errors and not wrong_or_rpc_errors and not rejected
        return {'mode': mode, 'workload': workload, 'offered_clients': n, 'admission_capacity': capacity,
            'admitted_at_dispatch_release': active_at_release, 'successful_correct_responses': successful,
            'http_rejections': rejected, 'transport_errors': transport_errors, 'wrong_or_rpc_errors': wrong_or_rpc_errors,
            'front_ingress': dict(frontend.counts), 'front_edge_metrics': [e.stats() for e in edges],
            'coordinator_ingress': dict(coordinator.counts) if coordinator else None,
            'coordinator_edge_metrics': center.stats() if center else None,
            'origin': dict(origin), 'peer': dict(peer), 'peer_json_bytes': peer_bytes,
            'eligible_for_equal_service_offload_comparison': eligible,
            'origin_reads_avoided_vs_one_per_completed_query': n - origin['state_reads'] if eligible else None,
            'origin_offload_percent': round(100 * (1 - origin['state_reads'] / n), 6) if eligible else None,
            'weighted_cost_scenarios': {str(w): str(score(origin['state_reads'], peer_bytes, w)) for w in (100, 200, 500)} if eligible else None,
            'latency_ms': {'p50': percentile(.5), 'p95': percentile(.95), 'p99': percentile(.99)},
            'drain_after_release_seconds': round(elapsed, 6), 'origin_read_emulated_delay_ms': 20,
            'origin_peak_by_client': [w.peak for w in origin_wrappers]}
    finally:
        gate.set()
        for task in tasks:
            if not task.done(): task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if frontend is not None: await frontend.close()
        if coordinator is not None: await coordinator.close()


async def experiment(n=1000):
    rows = []
    with LocalEvmOrigin() as node:
        for i in range(n): node._rpc('anvil_setBalance', [address(i), hex(1000 + i)])
        node._rpc('evm_mine', [1735689602])
        block = node._rpc('eth_getBlockByNumber', ['latest', False])
        node.block = {'blockHash': block['hash'], 'requireCanonical': False}
        for workload in ('hot-1', 'hot-8', 'unique', 'latest'):
            for mode in ('pass-through', 'isolated', 'shared'):
                row = await run_case(node, mode, workload, n); rows.append(row)
                print(json.dumps({'case': workload, 'mode': mode, 'success': row['successful_correct_responses'],
                    'origin_reads': row['origin']['state_reads'], 'offload_percent': row['origin_offload_percent']}), flush=True)
        overload = await run_case(node, 'shared', 'hot-1', n, min(64, n // 2))
        setup = dict(node.successful_setup_calls)
        readiness = node.readiness_successes
    return {'kind': 'real_loopback_http_load_with_local_evm_origin_not_wan_or_mainnet', 'status': 'completed',
        'clients_per_burst': n, 'rows': rows, 'overload_control': overload, 'local_fixture_setup': setup,
        'successful_local_readiness_calls': readiness,
        'public_rpc_calls': 0, 'public_transactions': 0, 'logical_partitions': 4,
        'partitioning_is_measured_neuropil_mapping': False, 'decentralized_deployment': False,
        'physical_nvme_io_measured': False, 'gpu_savings_measured': False, 'dollar_savings_measured': False,
        'implementation_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--out', default='results/tier-offload-1000.json')
    p.add_argument('--clients', type=int, default=1000); a = p.parse_args()
    if not 16 <= a.clients <= 1000: p.error('clients must be16..1000')
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE); needed = 4 * a.clients + 256
    if soft < needed:
        if hard != resource.RLIM_INFINITY and hard < needed: raise RuntimeError('Own-process file limit insufficient')
        resource.setrlimit(resource.RLIMIT_NOFILE, (needed, hard))
    path = Path(a.out); path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{"status":"running"}\n')
    try:
        report = asyncio.run(experiment(a.clients)); path.write_text(json.dumps(report, indent=2) + '\n')
    except BaseException as exc:
        path.write_text(json.dumps({'status': 'failed', 'failure_type': type(exc).__name__, 'public_rpc_calls': 0}) + '\n'); raise
    finally:
        if soft < needed: resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
