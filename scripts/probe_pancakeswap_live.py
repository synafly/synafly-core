#!/usr/bin/env python3
"""Opt-in PancakeSwap BSC mainnet storage probe; reads only, no signer or retries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from synafly_lab.edge import ReadEdge
from synafly_lab.rpc import BSC_GENESIS, HttpOrigin, NetworkIdentity, RpcError, encode, hex_data, quantity

UPSTREAM = 'https://bsc-dataseed.bnbchain.org'
PAIR = '0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae'
FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'
USDT = '0x55d398326f99059ff775485246999027b3197955'
WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
ALLOWED_METHODS = frozenset({'eth_chainId', 'eth_getBlockByNumber',
                             'eth_getCode', 'eth_getStorageAt'})
SOURCES = ('scripts/probe_pancakeswap_live.py', 'synafly_lab/rpc.py', 'synafly_lab/edge.py')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode_reserves(word):
    """Solidity slot 8: low uint112, next uint112, high uint32. No float conversion."""
    packed = int(hex_data(word, 32), 16)
    return {'reserve0_raw': str(packed & ((1 << 112) - 1)),
            'reserve1_raw': str((packed >> 112) & ((1 << 112) - 1)),
            'block_timestamp_last': packed >> 224}


def decode_address(word):
    word = hex_data(word, 32)
    require(word[2:26] == '0' * 24, 'Nonzero address padding in identity slot')
    return '0x' + word[26:]


class ObservedOrigin(HttpOrigin):
    """Observe existing bounded transport, including attempts during failed bootstrap."""
    def __init__(self, attempts, progress, timeout=8):
        self.attempts, self.progress = attempts, progress
        self.journal_lock = threading.Lock()
        super().__init__(UPSTREAM, NetworkIdentity(56, BSC_GENESIS), timeout=timeout)

    def call(self, method, params):
        if method not in ALLOWED_METHODS:
            raise RpcError(-32601, 'Probe method not allowed')
        with self.journal_lock:
            row = {'sequence': len(self.attempts) + 1,
                   'phase': self.progress.get('phase', 'bootstrap'),
                   'method': method, 'params': json.loads(encode(params)), 'outcome': 'started'}
            self.attempts.append(row)
        try:
            result = super().call(method, params)
        except RpcError as exc:
            row.update(outcome='error', error_code=exc.code)
            raise
        row.update(outcome='returned', result_sha256=hashlib.sha256(encode(result)).hexdigest())
        return result


def read_outcome(edge, params):
    try:
        return {'result': edge.read('eth_getStorageAt', params)}
    except RpcError as exc:
        return {'error_code': exc.code, 'failure': exc.message}


def burst(edge, params, clients):
    """Barrier aligns offered local threads; no delay is injected into the live origin."""
    barrier = threading.Barrier(clients, timeout=edge.origin.timeout + 1)

    def worker(item):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            return {'failure': 'Local launch barrier timed out'}
        return read_outcome(edge, item)

    with ThreadPoolExecutor(max_workers=clients) as pool:
        return list(pool.map(worker, params))


def delta(after, before):
    return {key: value - before[key] for key, value in after.items()}


def measure(origin, edge, params, clients, mode, round_index):
    before_origin, before_edge = origin.counters(), edge.stats()
    outcomes = burst(edge, [params for _ in range(clients)], clients)
    return {'mode': mode, 'round': round_index, 'offered_requests': clients,
            'completed_requests': sum('result' in item for item in outcomes),
            'outcomes': outcomes,
            'origin_metrics': delta(origin.counters(), before_origin),
            'edge_metrics': delta(edge.stats(), before_edge)}


def run_controls(origin, selector, expected, identity_words, report):
    """No persistent cache, no cross-slot reuse, canonical-required bypass."""
    edge = ReadEdge(origin, cache_entries=0, coalesce=True, max_inflight=2)
    before = origin.counters()
    row = {'outcomes': {}}
    report['controls'] = row
    params = [PAIR, '0x8', selector]
    row['outcomes']['sequential-repeat'] = [read_outcome(edge, params) for _ in range(2)]
    row['outcomes']['distinct-slots'] = burst(
        edge, [[PAIR, slot, selector] for slot in ['0x6', '0x7']], 2)
    canonical = {**selector, 'requireCanonical': True}
    row['outcomes']['canonical-required'] = burst(edge, [[PAIR, '0x8', canonical]] * 2, 2)
    row['origin_metrics'] = delta(origin.counters(), before)
    row['origin_reads'] = row['origin_metrics']['state_reads']
    row['edge_metrics'] = edge.stats()
    wanted = {'sequential-repeat': [expected, expected],
              'distinct-slots': [identity_words['0x6'], identity_words['0x7']],
              'canonical-required': [expected, expected]}
    require(all(row['outcomes'][name] == [{'result': value} for value in values]
                for name, values in wanted.items()), 'Negative control response mismatch or error')
    require(row['origin_reads'] == 6 and edge.stats()['coalesced_waiters'] == 0
            and edge.stats()['cache_hits'] == 0, 'Negative controls unexpectedly reused a read')


def experiment(origin, report, progress, clients, rounds):
    progress['phase'] = 'capture-latest'
    block = origin.call('eth_getBlockByNumber', ['latest', False])
    require(type(block) is dict, 'Malformed latest block')
    number = quantity(block.get('number'), 64)
    block_hash = hex_data(block.get('hash'), 32)
    timestamp = int(quantity(block.get('timestamp'), 64), 16)
    selector = {'blockHash': block_hash, 'requireCanonical': False}
    report['queried_block'] = {'number': number, 'number_decimal': int(number, 16),
                               'hash': block_hash, 'timestamp': timestamp,
                               'selector': selector, 'captured_at_utc': utc_now()}
    progress['phase'] = 'pool-identity'
    code = origin.call('eth_getCode', [PAIR, selector])
    require(code != '0x', 'Pool has no runtime code at the captured block')
    report['pool_runtime_code_sha256'] = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()
    identity_words = {slot: origin.call('eth_getStorageAt', [PAIR, slot, selector])
                      for slot in ['0x5', '0x6', '0x7']}
    actual = [decode_address(identity_words[slot]) for slot in ['0x5', '0x6', '0x7']]
    report['pool_identity'] = dict(zip(['factory', 'token0', 'token1'], actual))
    report['pool_identity']['storage_words'] = identity_words
    require(actual == [FACTORY, USDT, WBNB], 'Pool factory/token storage identity mismatch')

    edges = {name: ReadEdge(origin, cache_entries=0, coalesce=coalesce, max_inflight=clients)
             for name, coalesce in [('direct', False), ('single-flight', True)]}
    params = [PAIR, '0x8', selector]
    rows = report['bursts'] = []
    expected = None
    for round_index in range(rounds):
        order = ['direct', 'single-flight'] if round_index % 2 == 0 else ['single-flight', 'direct']
        for name in order:
            progress['phase'] = f'{name}-round-{round_index}'
            row = measure(origin, edges[name], params, clients, name, round_index)
            rows.append(row)
            require(row['completed_requests'] == clients, 'Burst contains failed or missing responses')
            values = [item['result'] for item in row['outcomes']]
            if expected is None:
                expected = values[0]
                report['storage_word'] = expected
                report['reserves'] = decode_reserves(expected)
            row['matching_responses'] = sum(value == expected for value in values)
            require(row['matching_responses'] == clients, 'Fixed-block reserve response mismatch')
            metrics = row['edge_metrics']
            reads = row['origin_metrics']['state_reads']
            require(reads == metrics['origin_reads'] and reads + metrics['coalesced_waiters'] == clients
                    and metrics['cache_hits'] == 0 and metrics['cache_entries'] == 0
                    and edges[name].stats()['inflight_keys'] == 0,
                    'Edge/transport accounting mismatch or unexpected cache use')
            require(name != 'direct' or reads == clients, 'Direct mode did not forward every read')

    report['matched_responses'] = sum(row['matching_responses'] for row in rows)
    report['sampled_responses'] = clients * rounds * 2
    report['all_sampled_responses_match'] = report['matched_responses'] == report['sampled_responses']
    report['measured_origin_reads'] = {name: sum(row['origin_metrics']['state_reads']
                                               for row in rows if row['mode'] == name) for name in edges}
    report['coalescing_observed'] = all(row['edge_metrics']['coalesced_waiters'] > 0
                                       for row in rows if row['mode'] == 'single-flight')
    progress['phase'] = 'negative-controls'
    run_controls(origin, selector, expected, identity_words, report)
    progress['phase'] = 'canonicality-recheck'
    canonical = origin.call('eth_getBlockByNumber', [number, False])
    report['canonicality_recheck'] = {'checked_at_utc': utc_now(), 'matched': False}
    require(type(canonical) is dict and quantity(canonical.get('number'), 64) == number
            and hex_data(canonical.get('hash'), 32) == block_hash,
            'Captured block no longer canonical according to provider')
    report['canonicality_recheck']['matched'] = True
    report['status'] = 'passed' if report['coalescing_observed'] else 'inconclusive'
    if report['status'] == 'inconclusive':
        report['failure'] = 'Not every single-flight burst overlapped; no automatic retry or manufactured delay'


def write_report(output, report):
    """Atomic replacement; invalidate old success before any network access."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.' + output.name, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write('\n')
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_probe(output, clients=4, rounds=2, timeout=8):
    report = {'schema': 'synafly.pancakeswap-live-probe.v1', 'status': 'running',
              'started_at_utc': utc_now(), 'upstream': UPSTREAM, 'chain_id': 56,
              'genesis_hash': BSC_GENESIS, 'pair': PAIR, 'storage_slot': '0x8',
              'blockchain_transactions_sent': 0, 'rpc_attempts': [],
              'implementation_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                         for name in SOURCES}}
    write_report(output, report)
    progress, origin = {'phase': 'configuration'}, None
    try:
        require(type(clients) is int and 2 <= clients <= 8, 'Clients must be an integer in [2, 8]')
        require(type(rounds) is int and 1 <= rounds <= 3, 'Rounds must be an integer in [1, 3]')
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 0.1 <= timeout <= 10,
                'Timeout must be finite in [0.1, 10] seconds')
        report['workload'] = {'clients_per_burst': clients, 'rounds': rounds, 'timeout_seconds': timeout,
                              'maximum_rpc_attempts': 14 + 2 * clients * rounds,
                              'cache_entries': 0, 'injected_origin_delay_seconds': 0,
                              'retries': 0, 'allowed_rpc_methods': sorted(ALLOWED_METHODS)}
        progress['phase'] = 'bootstrap'
        origin = ObservedOrigin(report['rpc_attempts'], progress, timeout)
        experiment(origin, report, progress, clients, rounds)
        require(len(report['rpc_attempts']) == origin.counters()['rpc_calls'], 'RPC journal count mismatch')
        require(len(report['rpc_attempts']) <= report['workload']['maximum_rpc_attempts'], 'RPC budget exceeded')
    except (ValueError, threading.BrokenBarrierError) as exc:
        report.update(status='failed', failure=str(exc), error_code=getattr(exc, 'code', None))
    except KeyboardInterrupt:
        report.update(status='interrupted', failure='Operator interrupted the probe')
    except Exception as exc:
        # Top-level artifact boundary: do not publish exception contents or leave stale success.
        report.update(status='failed', failure='Unexpected probe exception', exception_type=type(exc).__name__)
    finally:
        report['phase'] = progress['phase']
        report['finished_at_utc'] = utc_now()
        report['rpc_attempts_total'] = len(report['rpc_attempts'])
        if origin is not None:
            report['origin_metrics'] = origin.counters()
        write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='results/bsc-pancakeswap-live-probe.json')
    parser.add_argument('--clients', type=int, default=4)
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--timeout', type=float, default=8)
    args = parser.parse_args()
    report = run_probe(args.out, args.clients, args.rounds, args.timeout)
    print(json.dumps({key: report[key] for key in ['status', 'rpc_attempts_total',
                       'blockchain_transactions_sent', 'measured_origin_reads',
                       'matched_responses', 'sampled_responses', 'failure'] if key in report}, indent=2))
    return 0 if report['status'] == 'passed' else 130 if report['status'] == 'interrupted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
