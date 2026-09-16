#!/usr/bin/env python3
"""Audit recorded concurrency, origin/peer counters and scenario arithmetic."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmark_tier_offload import ROOT, SOURCES
from synafly_lab.tier_cost import score


def verify(report):
    if report['status'] != 'completed' or report['clients_per_burst'] != 1000: raise ValueError('Load scope')
    if report['implementation_sha256'] != {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in SOURCES}:
        raise ValueError('Load source binding')
    if report['public_rpc_calls'] or report['public_transactions']: raise ValueError('Public network scope')
    if report['local_fixture_setup'] != {'anvil_setBalance': 1000, 'evm_mine': 1, 'eth_getBlockByNumber': 2}:
        raise ValueError('Fixture setup accounting')
    if report['successful_local_readiness_calls'] != 1: raise ValueError('Readiness accounting')
    for name in ('partitioning_is_measured_neuropil_mapping', 'decentralized_deployment',
                 'physical_nvme_io_measured', 'gpu_savings_measured', 'dollar_savings_measured'):
        if report[name] is not False: raise ValueError('Unmeasured scope promoted')
    expected = {(w, m) for w in ('hot-1', 'hot-8', 'unique', 'latest') for m in ('pass-through', 'isolated', 'shared')}
    seen = set()
    for row in report['rows']:
        key = row['workload'], row['mode']
        if key not in expected or key in seen: raise ValueError('Duplicate/unexpected load case')
        seen.add(key)
        if row['offered_clients'] != 1000 or row['eligible_for_equal_service_offload_comparison'] is not True:
            raise ValueError('Equal-service workload scope')
        if row['successful_correct_responses'] != 1000 or any(row[k] for k in ('http_rejections', 'transport_errors', 'wrong_or_rpc_errors')):
            raise ValueError('Incomplete service must not count as offload')
        ingress = row['front_ingress']
        if row['admitted_at_dispatch_release'] != 1000 or ingress['peak_admitted'] != 1000 or ingress['received_requests'] != 1000:
            raise ValueError('Offered count is not demonstrated concurrency')
        if not 1 <= ingress['peak_workers'] <= 16 or ingress.get('responses_200') != 1000:
            raise ValueError('Worker/response accounting')
        if any(ingress.get(k, 0) for k in ('internal_errors', 'transport_errors', 'bad_requests', 'admission_rejections')):
            raise ValueError('Ingress errors')
        front = row['front_edge_metrics']
        if len(front) != 4 or any(e['read_requests'] != 250 for e in front): raise ValueError('Partition workload coverage')
        if any(e['origin_errors'] or e['capacity_rejections'] for e in front): raise ValueError('Edge error accounting')
        origin = row['origin']; expected_reads = 1000
        if row['workload'] == 'hot-1': expected_reads = {'pass-through': 1000, 'isolated': 4, 'shared': 1}[row['mode']]
        if row['workload'] == 'hot-8': expected_reads = {'pass-through': 1000, 'isolated': 32, 'shared': 8}[row['mode']]
        if origin['state_reads'] != expected_reads or origin['rpc_calls'] != origin['state_reads'] + origin['metadata_calls']:
            raise ValueError('Origin accounting')
        if origin['metadata_calls'] != (2 if row['mode'] == 'shared' else 8): raise ValueError('Bootstrap accounting')
        peer = row['peer']; peer_bytes = peer.get('request_json_bytes', 0) + peer.get('response_json_bytes', 0)
        if row['peer_json_bytes'] != peer_bytes: raise ValueError('Peer MB accounting')
        if row['mode'] == 'shared':
            center = row['coordinator_edge_metrics']; wire = row['coordinator_ingress']
            if peer.get('calls', 0) != center['read_requests'] or center['read_requests'] != wire.get('received_requests', 0):
                raise ValueError('Cross-partition forwarding')
            if peer_bytes != wire.get('request_body_bytes', 0) + wire.get('response_body_bytes', 0): raise ValueError('Peer transport accounting')
            if row['workload'] == 'latest':
                if center['origin_reads'] or peer_bytes or peer.get('direct_selector_bypasses') != 1000: raise ValueError('Mutable bypass')
            elif center['origin_reads'] != expected_reads: raise ValueError('Coordinator reads')
        elif peer_bytes: raise ValueError('Unexpected peer traffic')
        if row['origin_reads_avoided_vs_one_per_completed_query'] != 1000 - expected_reads:
            raise ValueError('Avoided reads')
        if row['origin_offload_percent'] != round(100 * (1 - expected_reads / 1000), 6): raise ValueError('Offload arithmetic')
        if row['weighted_cost_scenarios'] != {str(w): str(score(expected_reads, peer_bytes, w)) for w in (100, 200, 500)}:
            raise ValueError('Weighted scenario arithmetic')
    if seen != expected: raise ValueError('Missing workload control')
    overload = report['overload_control']
    if overload['successful_correct_responses'] != 64 or overload['http_rejections'] != 936 or overload['transport_errors'] or overload['wrong_or_rpc_errors']:
        raise ValueError('Overload accounting')
    if overload['offered_clients'] != 1000 or overload['front_ingress']['peak_admitted'] != 64 or overload['front_ingress']['admission_rejections'] != 936:
        raise ValueError('Overload admission accounting')
    if overload['eligible_for_equal_service_offload_comparison'] or overload['origin_offload_percent'] is not None or overload['weighted_cost_scenarios'] is not None:
        raise ValueError('Rejections misrepresented as savings')
    return {'fully_served_bursts_verified': len(seen), 'correct_responses': 12000,
            'actual_peak_admitted_per_normal_burst': 1000, 'overload_rejections_excluded': 936,
            'scope': 'Saved load evidence and arithmetic audit, not independent hardware cost measurement.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--report', default=str(ROOT / 'results/tier-offload-1000.json'))
    args = p.parse_args(); print(json.dumps(verify(json.loads(Path(args.report).read_text()))))
