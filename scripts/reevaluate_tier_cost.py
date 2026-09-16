#!/usr/bin/env python3
"""Reweight published counters; no historical simulator/import is required."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synafly_lab.tier_cost import score, compare

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'results/tiered-cost-reevaluation.json'
# Export pin protects the input observations, not the interpretation/weights.
SNAPSHOT_SHA256 = '7391c2011f6b2afc164f6a5c98f2bcbaeec02665a72986ae7c37153f2ce55bf9'
AXES = ('layout', 'seed', 'scenario', 'failure', 'topology', 'cooperative')
DOMAINS = (('balanced-random', 'class-colocated'), (0, 1),
           ('global-hot', 'regional-hot', 'block-churn', 'mostly-mutable'),
           ('none', 'random-10pct', 'affinity-hubs', 'region-zero'),
           ('geographic', 'affinity', 'degree-rewired-11', 'degree-rewired-23', 'direct-owner-reference'),
           (False, True))


def validate_snapshot(snapshot):
    if snapshot.get('kind') != 'historical_discrete_simulation_counter_export_not_a_new_network_run' or snapshot.get('original_simulator_in_this_release') is not False:
        raise ValueError('Historical snapshot scope')
    rows = snapshot.get('rows', [])
    if len(rows) != 640: raise ValueError('Complete 640-row snapshot required')
    expected = set(itertools.product(*DOMAINS)); indexed = {}; outcomes = {}
    for row in rows:
        case = row['case']
        if set(case) != set(AXES) or type(case['seed']) is not int or type(case['cooperative']) is not bool:
            raise ValueError('Counter snapshot case schema')
        key = tuple(case[k] for k in AXES)
        if key not in expected or key in indexed: raise ValueError('Duplicate/unexpected counter case')
        if row['requests'] != 256 or type(row['requests']) is not int or row['all_expected_values_match'] is not True:
            raise ValueError('Counter workload/correctness scope')
        score(row['origin_reads'], row['peer_bytes'])  # Physical count validation.
        if row['origin_reads'] > row['requests']: raise ValueError('Origin count exceeds workload')
        for name in ('workload_sha256', 'outcomes_sha256'):
            if not re.fullmatch('[0-9a-f]{64}', row[name]): raise ValueError('Counter digest format')
        pair = row['workload_sha256'], row['outcomes_sha256']
        if outcomes.setdefault(key[:3], pair) != pair: raise ValueError('Unequal inputs/outputs across controls')
        indexed[key] = row
    if set(indexed) != expected: raise ValueError('Missing counter case')
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != SNAPSHOT_SHA256: raise ValueError('Historical counter export pin')
    return indexed


def evaluate(snapshot=None):
    if snapshot is None: snapshot = json.loads(REPORT.read_text())['cooperative_snapshot']
    indexed = validate_snapshot(snapshot)
    names = ['results/edge-http.json', 'results/bsc-read-probe.json']
    edge, bsc = [json.loads((ROOT / n).read_text()) for n in names]
    weights = (100, 200, 500); arms = {}
    for name, topology, sharing in [('A', 'geographic', False), ('B', 'affinity', False),
                                    ('C', 'geographic', True), ('D', 'affinity', True)]:
        rows = [r for k, r in indexed.items() if k[0] == 'balanced-random' and k[3] == 'none'
                and k[4] == topology and k[5] == sharing]
        arms[name] = {'origin_reads': sum(r['origin_reads'] for r in rows),
                      'peer_bytes': sum(r['peer_bytes'] for r in rows),
                      'client_requests': sum(r['requests'] for r in rows), 'trial_count': len(rows)}
    comparisons = []
    for first, last in [('A', 'C'), ('B', 'D'), ('A', 'D'), ('C', 'D')]:
        a, b = arms[first], arms[last]
        comparisons.append({'comparison': first + '->' + last,
            'scenarios': [compare(a['origin_reads'], a['peer_bytes'], b['origin_reads'], b['peer_bytes'], w) for w in weights]})
    outcomes = {}
    for w in weights:
        counts = {'better': 0, 'worse': 0, 'tied': 0}
        for key, a in indexed.items():
            if key[-1]: continue
            b = indexed[(*key[:-1], True)]
            delta = score(b['origin_reads'], b['peer_bytes'], w) - score(a['origin_reads'], a['peer_bytes'], w)
            counts['better' if delta < 0 else 'worse' if delta > 0 else 'tied'] += 1
        outcomes[str(w)] = counts
    old = edge['results'][0]['measured']['origin']
    new = next(r for r in edge['results'] if r['cache_enabled'] and r['coalescing_enabled'])['measured']['origin']
    p, c = bsc['results'][0]['origin_metrics'], bsc['results'][1]['origin_metrics']
    other = []
    for label, before, after in [('v0.2-local-http', old, new), ('saved-public-bsc-probe', p, c)]:
        other.append({'evidence': label, 'state_reads_before': before['state_reads'], 'state_reads_after': after['state_reads'],
            'rpc_calls_before': before['rpc_calls'], 'rpc_calls_after': after['rpc_calls'],
            'peer_traffic_measured': False, 'score_scope': 'origin component only; no invented peer-traffic measurement',
            'origin_component_scenarios': [compare(before['state_reads'], 0, after['state_reads'], 0, w) for w in weights]})
    return {'kind': 'user_directed_relative_cost_sensitivity_not_measured_prices',
        'formula': 'origin_weight * origin_state_reads + peer_json_bytes / 1000000',
        'weights': list(weights), 'default_origin_weight': 200, 'peer_weight_per_mb': 1,
        'mb_definition_bytes': 1_000_000, 'currency': None, 'monthly_node_price_verified': False,
        'cooperative_snapshot': snapshot, 'counter_export_sha256': SNAPSHOT_SHA256,
        'historical_scope': 'Complete exported counter table; arithmetic/coverage replay only. Original discrete simulator is not included or rerun by this release.',
        'reweighted_trial_count': len(indexed), 'healthy_balanced_arms': arms, 'healthy_comparisons': comparisons,
        'cooperation_pairs_all_layouts_failures': outcomes, 'other_evidence': other,
        'limits': ['Weights are declared scenarios, not empirical lower bounds or dollar prices.',
                   'A 200-per-read / 1-per-MB score assigns one read the cost of 200 MB of peer traffic.',
                   'Request reduction is not equal to CPU/NVMe/GPU or fixed monthly bill reduction.',
                   'All 640 exported counter rows are preserved, including failure and negative controls.',
                   'Counter digests provide reproducibility/integrity, not independent verification of the original simulation.',
                   'Ordinary coordination gains do not establish anatomical superiority.'],
        'source_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in names},
        'implementation_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in ['scripts/reevaluate_tier_cost.py', 'synafly_lab/tier_cost.py']}}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', help='Write a recomputed report to a separate path; default audits the committed report')
    args = p.parse_args(); report = evaluate()
    if args.out:
        path = Path(args.out); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n')
    elif report != json.loads(REPORT.read_text()):
        raise ValueError('Saved cost report differs from recomputation')
    print(json.dumps({'counter_rows_verified': report['reweighted_trial_count'],
        'arms': report['healthy_balanced_arms'], 'all_case_pairs': report['cooperation_pairs_all_layouts_failures'],
        'scope': report['historical_scope']}))
