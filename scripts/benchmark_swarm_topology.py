#!/usr/bin/env python3
"""Sequential topology/cache simulation and historical-result audit; no BSC I/O."""
import argparse
from collections import Counter, OrderedDict
from decimal import Decimal
import hashlib
import heapq
import json
from pathlib import Path
import random
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synafly_lab.rpc import encode
from synafly_lab.tier_cost import score
from synafly_lab.topology import rewire

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'results/swarm-topology-benchmark.json'
INPUT_SHA256 = '2817fed9bc7b900c15b8c286da8832afaa4a10ef2b166a3056b6d67acd84187f'
SEEDS = (42, 43, 44, 45, 46)
SOURCES = ('scripts/benchmark_swarm_topology.py', 'synafly_lab/tier_cost.py',
           'synafly_lab/topology.py', 'synafly_lab/model.py', 'synafly_lab/canonical.py',
           'synafly_lab/rpc.py', 'docs/swarm-topology-design.md')
LABELS = {'isolated': 'Isolated Edges (No Swarm)', 'flood': 'Naive Gossip Swarm (Flooding)',
          'ring': 'Structured Ring DHT', 'geographic': 'Geographic Cluster Overlay',
          'affinity': 'Drosophila Connectome (SynaFly)'}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Integer outside declared bounds')
    return value


def validate_inputs(inputs):
    if inputs.get('schema') != 'synafly.swarm-inputs.v1': raise ValueError('Input schema')
    data = inputs['aggregation']; groups = data['groups']; n = len(groups)
    integer(n, 7, 256)
    if [g['id'] for g in groups] != list(range(n)): raise ValueError('Group identity/order')
    if sum(g['neurons'] for g in groups) != data['selected_neurons']: raise ValueError('Neuron accounting')
    seen = set(); weights = rows = 0
    for a, b, w, count in data['edges']:
        integer(a, 0, n - 1); integer(b, 0, n - 1)
        integer(w, 1, 2**63 - 1); integer(count, 1, 2**63 - 1)
        if a == b or (a, b) in seen: raise ValueError('Duplicate/self aggregation edge')
        seen.add((a, b)); weights += w; rows += count
    if weights + sum(g['within_group_weight'] for g in groups) != data['retained_synapse_weight']:
        raise ValueError('Synapse accounting')
    if rows + sum(g['within_group_rows'] for g in groups) != data['retained_connection_rows']:
        raise ValueError('Connection accounting')
    keys = inputs['key_digests']
    if len(keys) != 4 or len(set(keys)) != 4 or any(not re.fullmatch('[0-9a-f]{64}', k) for k in keys):
        raise ValueError('Four distinct opaque key digests required')
    if digest(inputs) != INPUT_SHA256: raise ValueError('Pinned input export mismatch')
    return data


class RoleNetwork:
    """Directed overlay with global, static least-cost routes. No route repair."""
    def __init__(self, name, regions, edges):
        self.name, self.regions = name, tuple(regions); self.n = len(self.regions)
        integer(self.n, 2, 256)
        for region in self.regions: integer(region, 0, 3)
        pairs = [tuple(pair) for pair in edges]
        if len(pairs) != len(set(pairs)): raise ValueError('Duplicate overlay edge')
        self.edges = tuple(sorted(pairs)); adjacent = [[] for _ in self.regions]
        for a, b in self.edges:
            integer(a, 0, self.n - 1); integer(b, 0, self.n - 1)
            if a == b: raise ValueError('Self overlay edge')
            adjacent[a].append(b)
        self.adjacent = tuple(tuple(row) for row in adjacent); self._paths = {}

    def path(self, source, target):
        integer(source, 0, self.n - 1); integer(target, 0, self.n - 1)
        key = source, target
        if key in self._paths: return self._paths[key]
        queue = [(0, (source,), source)]; best = {source: (0, (source,))}
        while queue:
            cost, path, node = heapq.heappop(queue)
            if best[node] != (cost, path): continue
            if node == target:
                self._paths[key] = path; return path
            for other in self.adjacent[node]:
                candidate = cost + (1 if self.regions[node] == self.regions[other] else 8), path + (other,)
                if other not in best or candidate < best[other]:
                    best[other] = candidate; heapq.heappush(queue, (*candidate, other))
        self._paths[key] = None; return None

    def profile(self):
        paths = [self.path(a, b) for a in range(self.n) for b in range(self.n) if a != b]
        reachable = [p for p in paths if p is not None]
        hops = sum(len(p) - 1 for p in reachable)
        return {'name': self.name, 'roles': self.n, 'directed_arcs': len(self.edges),
                'out_degrees': [len(row) for row in self.adjacent],
                'in_degrees': [sum(b == i for _, b in self.edges) for i in range(self.n)],
                'topology_sha256': digest([self.regions, self.edges]),
                'reachable_ordered_pairs': len(reachable), 'possible_ordered_pairs': len(paths),
                'weighted_route_hop_sum': hops,
                'mean_hops_on_least_cost_routes': round(hops / len(reachable), 8) if reachable else None}


def build_networks(data):
    n = len(data['groups']); order = list(range(n)); random.Random(0).shuffle(order)
    regions = [0] * n
    for i, node in enumerate(order): regions[node] = i % 4
    measured = {(a, b): w for a, b, w, _ in data['edges']}
    affinity, geographic, backbone, fill = [], [], set(), set()
    for a in range(n):
        base = {(a - 1) % n, (a + 1) % n}; backbone.update((a, b) for b in base)
        selected = set(base)
        for b in sorted((b for b in range(n) if b != a and (a, b) in measured), key=lambda b: (-measured[a, b], b)):
            if len(selected) == 6: break
            selected.add(b)
        for b in sorted((b for b in range(n) if b != a), key=lambda b: digest(('fill', a, b))):
            if len(selected) == 6: break
            if b not in selected: selected.add(b); fill.add((a, b))
        affinity.extend((a, b) for b in sorted(selected))
        selected = set(base)
        for same in (True, False):
            candidates = sorted((b for b in range(n) if b != a and b not in selected and
                                 (regions[a] == regions[b]) == same), key=lambda b: digest(('geo', a, b)))
            selected.update(candidates[:2])
        for b in sorted((b for b in range(n) if b != a), key=lambda b: digest(('fill', a, b))):
            if len(selected) == 6: break
            selected.add(b)
        geographic.extend((a, b) for b in sorted(selected))
    ring = sorted({(i, (i + delta) % n) for i in range(n) for delta in (1, -1, n//4, -(n//4), n//2, 2)})
    nets = {'geographic': RoleNetwork('geographic', regions, geographic),
            'affinity': RoleNetwork('affinity', regions, affinity),
            'ring': RoleNetwork('ring', regions, ring),
            'flood': RoleNetwork('flood', regions, [(a, b) for a in range(n) for b in range(n) if a != b])}
    graph = {'schema': 'synafly.graph.v1', 'nodes': [str(i) for i in range(n)],
             'edges': [[a, b, 1] for a, b in sorted(affinity)]}
    for seed in (11, 23):
        changed, swaps = rewire(graph, seed)
        name = 'degree-rewired-' + str(seed)
        nets[name] = RoleNetwork(name, regions, [(a, b) for a, b, _ in changed['edges']])
    provenance = {'engineered_backbone': [list(edge) for edge in sorted(backbone)],
                  'engineered_fill': [list(edge) for edge in sorted(fill)],
                  'affinity_arcs_in_measured_aggregation': sum(e in measured for e in affinity),
                  'original_neuron_graph_preserved': False}
    return nets, provenance


class SwarmSimulation:
    """Serial cache lookup tokens; transport bytes are assigned constants, not measured."""
    def __init__(self, network, mode='routed', cache_capacity=16, policy='matched'):
        if mode not in ('isolated', 'flood', 'routed') or policy not in ('legacy', 'matched'):
            raise ValueError('Simulation mode/policy')
        integer(cache_capacity, 1, 256)
        self.net, self.mode, self.capacity, self.policy = network, mode, cache_capacity, policy
        self.caches = [OrderedDict() for _ in range(network.n)]
        self.peak_entries = 0; self.counts = Counter()

    def put(self, node, key, value):
        cache = self.caches[node]
        cache[key] = value; cache.move_to_end(key)
        while len(cache) > self.capacity: cache.popitem(last=False)
        self.peak_entries = max(self.peak_entries, len(cache))

    def origin(self, node, key, reason, cache=True):
        self.counts['origin_reads'] += 1; self.counts[reason] += 1
        value = digest(['synthetic-value', key])
        if cache: self.put(node, key, value)
        return value

    def process_request(self, entry, key, failed_nodes=()):
        integer(entry, 0, self.net.n - 1)
        if type(key) is not str or not re.fullmatch('[0-9a-f]{64}', key): raise ValueError('Opaque SHA-256 key required')
        failed = frozenset(failed_nodes)
        for node in failed: integer(node, 0, self.net.n - 1)
        c = self.counts; c['requests'] += 1
        if entry in failed:
            # Modeled external caller bypass; a crashed process does not execute this.
            return self.origin(entry, key, 'failed_ingress_origin', cache=False)
        cache = self.caches[entry]
        if key in cache:
            cache.move_to_end(key); c['cache_hits'] += 1; c['local_hits'] += 1
            return cache[key]
        if self.mode == 'isolated': return self.origin(entry, key, 'isolated_origin')
        if self.mode == 'flood':
            peers = [p for p in range(self.net.n) if p != entry and (self.policy == 'matched' or p not in failed)]
            for peer in peers:
                c['peer_messages'] += 1; c['peer_bytes'] += 128
                if peer not in failed:
                    c['peer_messages'] += 1; c['peer_bytes'] += 180
                else: c['failed_contacts'] += 1
            found = next((p for p in peers if p not in failed and key in self.caches[p]), None)
            if found is None: return self.origin(entry, key, 'flood_origin')
            value = self.caches[found][key]; c['cache_hits'] += 1; c['peer_hits'] += 1
            if self.policy == 'matched': self.put(entry, key, value)
            return value
        owner = int(key[:8], 16) % self.net.n
        path = self.net.path(entry, owner)
        if path is None: return self.origin(entry, key, 'unreachable_origin')
        bad = any(node in failed for node in path)
        if self.policy == 'legacy':
            if bad: return self.origin(entry, key, 'failed_route_origin')
            c['peer_messages'] += (len(path) - 1) * 2; c['peer_bytes'] += (len(path) - 1) * 308
        else:
            for node in path[1:]:
                c['peer_messages'] += 1; c['peer_bytes'] += 128
                if node in failed:
                    c['failed_contacts'] += 1
                    return self.origin(entry, key, 'failed_route_origin')
            c['peer_messages'] += len(path) - 1; c['peer_bytes'] += (len(path) - 1) * 180
        c['successful_route_hops'] += len(path) - 1; c['successful_routes'] += 1
        coordinator = self.caches[owner]
        if key in coordinator:
            value = coordinator[key]; coordinator.move_to_end(key)
            c['cache_hits'] += 1; c['peer_hits'] += 1
        else:
            value = self.origin(owner, key, 'owner_origin')
        self.put(entry, key, value)
        return value


def workload(keys, n, seed, kind='hot-pinned', count=2048):
    integer(n, 2, 256); integer(seed, 0, 2**32 - 1); integer(count, 1, 10000)
    if kind not in ('hot-pinned', 'unique-pinned', 'block-changing'): raise ValueError('Workload kind')
    rng = random.Random(seed); requests = []
    for i in range(count):
        entry, key = rng.randint(0, n - 1), rng.choice(keys)
        if kind == 'unique-pinned': key = digest(['unique', seed, i])
        if kind == 'block-changing': key = digest([key, 'block', i // 64])
        requests.append((entry, key))
    return requests, rng


def run_case(net, mode, policy, requests, failed=()):
    sim = SwarmSimulation(net, mode, policy=policy)
    responses = [sim.process_request(entry, key, failed) for entry, key in requests]
    expected = [digest(['synthetic-value', key]) for _, key in requests]
    if responses != expected: raise ValueError('Incorrect simulated value')
    names = ('requests', 'origin_reads', 'cache_hits', 'local_hits', 'peer_hits', 'peer_messages', 'peer_bytes',
             'failed_ingress_origin', 'isolated_origin', 'flood_origin', 'owner_origin', 'failed_route_origin',
             'unreachable_origin', 'failed_contacts', 'successful_route_hops', 'successful_routes')
    m = {name: sim.counts[name] for name in names}
    if m['origin_reads'] + m['cache_hits'] != len(requests): raise ValueError('Read accounting')
    return {'metrics': m, 'coalesced_waiters': 0, 'peak_cache_entries_per_role': sim.peak_entries,
            'all_fixture_values_match': True, 'outputs_sha256': digest(responses),
            'offload_percent': round(100 * (1 - m['origin_reads'] / len(requests)), 6),
            'scenario_scores': {str(w): str(score(m['origin_reads'], m['peer_bytes'], w)) for w in (100, 200, 500)}}


def historical_row(condition, name, row):
    m = row['metrics']; offloaded = m['requests'] - m['origin_reads']
    return {'condition': condition, 'topology': LABELS[name], 'total_requests': m['requests'],
            'bsc_origin_reads': m['origin_reads'], 'bsc_read_offload_pct': round(row['offload_percent'], 2),
            'peer_messages': m['peer_messages'], 'peer_json_mb': round(m['peer_bytes'] / 1_000_000, 4),
            'bandwidth_amplification_bytes_per_read': round(m['peer_bytes'] / max(1, offloaded), 2),
            'bsc_system_cost_score_200': float(round(Decimal(row['scenario_scores']['200']), 2)),
            'coalesced_waiters': 0, 'cache_hits': m['cache_hits']}


def run_benchmark(inputs=None):
    if inputs is None: inputs = json.loads(REPORT.read_text())['inputs']
    data = validate_inputs(inputs); nets, arc_provenance = build_networks(data); n = len(data['groups'])
    names = ('isolated', 'flood', 'ring', 'geographic', 'affinity')
    expanded = (*names, 'degree-rewired-11', 'degree-rewired-23')
    def network(name): return nets['geographic' if name == 'isolated' else name]
    def mode(name): return name if name in ('isolated', 'flood') else 'routed'
    requests, rng = workload(inputs['key_digests'], n, 42)
    failed = sorted(rng.sample(range(n), n // 10)); original = []; legacy_detail = []
    for condition, bad in [('Normal Operating Conditions', ()), ('10% Swarm Node Failure (Churn)', failed)]:
        for name in names:
            row = run_case(network(name), mode(name), 'legacy', requests, bad)
            original.append(historical_row(condition, name, row))
            legacy_detail.append({'topology': name, 'failed_nodes': list(bad), **row})
    if original != inputs['legacy_rows']: raise ValueError('Historical ten-row replay differs')
    trials = []
    for seed in SEEDS:
        requests, rng = workload(inputs['key_digests'], n, seed)
        # All topologies get exactly the same physical failures. First five match
        # the original seed-42 sample; the sixth is a separately labeled control.
        failed5 = rng.sample(range(n), n // 10)
        sixth = rng.choice([i for i in range(n) if i not in failed5])
        for bad in ([], sorted(failed5), sorted([*failed5, sixth])):
            for name in expanded:
                trials.append({'seed': seed, 'topology': name, 'workload': 'hot-pinned',
                    'failed_nodes': bad, 'failed_percent': round(len(bad) / n * 100, 6),
                    'workload_sha256': digest(requests),
                    **run_case(network(name), mode(name), 'matched', requests, bad)})
    controls = []
    for kind in ('unique-pinned', 'block-changing'):
        requests, _ = workload(inputs['key_digests'], n, 42, kind)
        for name in expanded:
            controls.append({'seed': 42, 'topology': name, 'workload': kind, 'failed_nodes': [],
                'workload_sha256': digest(requests), **run_case(network(name), mode(name), 'matched', requests)})
    profiles = {name: net.profile() for name, net in nets.items()}
    indexed = {(r['seed'], len(r['failed_nodes']), r['topology']): r for r in trials}
    paired = {}
    for other in expanded:
        if other == 'affinity': continue
        counts = Counter()
        for seed in SEEDS:
            for failed_count in (0, 5, 6):
                a = Decimal(indexed[seed, failed_count, 'affinity']['scenario_scores']['200'])
                b = Decimal(indexed[seed, failed_count, other]['scenario_scores']['200'])
                counts['lower' if a < b else 'higher' if a > b else 'tied'] += 1
        paired[other] = {k: counts[k] for k in ('lower', 'higher', 'tied')}
    g = legacy_detail[1]['metrics']; a = legacy_detail[4]['metrics']
    return {'experiment': 'sequential_swarm_topology_reproduction_and_matched_controls',
        'inputs': inputs, 'input_sha256': INPUT_SHA256, 'roles': n,
        'scope': {'kind': 'deterministic_serial_simulation', 'public_rpc_calls': 0, 'transactions': 0,
                  'http_load_generated': False, 'nvme_io_measured': False, 'biological_self_healing_implemented': False,
                  'dynamic_churn_modeled': False, 'singleflight_overlap_modeled': False,
                  'request_bytes_per_hop_assumed': 128, 'reply_bytes_per_hop_assumed': 180,
                  'model_region_costs': {'same_region': 1, 'cross_region': 8},
                  'failure_control_scope': 'Fixed failed-role masks before run; external caller bypass for failed ingress; no repair/discovery cost.'},
        'arc_provenance': arc_provenance, 'topology_profiles': profiles,
        'historical_replay': {'rows': original, 'details': legacy_detail, 'exact_ten_row_match': True,
            'original_failure_count': len(failed), 'original_failure_percent': round(len(failed) / n * 100, 6),
            'healthy_affinity_vs_naive_flood_assumed_byte_reduction_percent': round(100 * (1 - a['peer_bytes'] / g['peer_bytes']), 6),
            'warning': 'Historical BSC/DEX/churn labels are preserved only as audit provenance; counters are synthetic and do not measure these claims.'},
        'matched_policy_trials': trials, 'negative_controls': controls,
        'matched_policy_affinity_score_vs_controls': paired,
        'implementation_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-export', help='Optional pinned input export; default uses the bundled report inputs')
    p.add_argument('--out', help='Write a recomputed report separately; default verifies the committed report')
    args = p.parse_args()
    inputs = json.loads(Path(args.input_export).read_text()) if args.input_export else None
    report = run_benchmark(inputs)
    if args.out:
        path = Path(args.out); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n')
    elif report != json.loads(REPORT.read_text()): raise ValueError('Saved report differs from exact replay')
    print(json.dumps({'historical_rows_reproduced': 10, 'matched_policy_trials': len(report['matched_policy_trials']),
        'negative_controls': len(report['negative_controls']), 'roles': report['roles'],
        'historical_byte_reduction_percent': report['historical_replay']['healthy_affinity_vs_naive_flood_assumed_byte_reduction_percent'],
        'affinity_score_vs_controls': report['matched_policy_affinity_score_vs_controls'], 'scope': report['scope']}))
