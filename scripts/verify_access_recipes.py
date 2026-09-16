#!/usr/bin/env python3
"""Rebuild recipes/predictions and independently rederive evidence accounting."""
import argparse
import hashlib
import json
import re
from unittest.mock import patch
from pathlib import Path
from demo_access_recipes import ROOT, verify, digest, METHODS
from synafly_lab import access_recipes as recipes


def recorded_keccak_provider(corpus):
    """Finite recorded wire answers, not an independent cryptographic implementation."""
    fixtures = corpus.get('keccak_wire_fixtures')
    if type(fixtures) is not list or not 1 <= len(fixtures) <= 4096:
        raise ValueError('Keccak fixture bound')
    lookup = {}
    for row in fixtures:
        if type(row) is not dict or set(row) != {'input', 'output'}:
            raise ValueError('Keccak fixture schema')
        a, b = row['input'], row['output']
        if (type(a) is not str or not re.fullmatch(r'0x(?:[0-9a-f]{2}){0,4096}', a)
                or type(b) is not str or not re.fullmatch(r'0x[0-9a-f]{64}', b)):
            raise ValueError('Keccak fixture encoding')
        key = bytes.fromhex(a[2:])
        if key in lookup: raise ValueError('Duplicate Keccak fixture')
        lookup[key] = int(b, 16)
    if lookup.get(b'') != int('c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470', 16):
        raise ValueError('Known Ethereum Keccak vector')
    def provider(data):
        if type(data) is not bytes or data not in lookup:
            raise ValueError('Unrecorded Keccak input; use cast for new materialization')
        return lookup[data]
    return provider


def audit(corpus, report, with_cast=False):
    provider = recorded_keccak_provider(corpus)
    if report['keccak_fixture_count'] != len(corpus['keccak_wire_fixtures']): raise ValueError('Keccak coverage')
    if with_cast:
        for row in corpus['keccak_wire_fixtures']:
            data = bytes.fromhex(row['input'][2:])
            if recipes.keccak(data) != provider(data): raise ValueError('Independent cast Keccak mismatch')
    with patch.object(recipes, 'keccak', new=provider):
        replay = verify(corpus, report)
    if report['kind'] != 'actual_evm_traces_synthetic_contracts_not_bsc_native_cost': raise ValueError('Experiment scope')
    if report['public_rpc_calls'] or report['public_transactions']: raise ValueError('Public chain scope')
    queries = corpus['heldout_inputs']; predictions = corpus['frozen_predictions']; references = corpus['heldout_reference_results']
    if len(queries) != 256 or len(references) != len(queries) or [r['query'] for r in references] != list(range(len(queries))):
        raise ValueError('Query/reference coverage')
    observations = corpus['prefetch_observations']
    if len(observations) != len(predictions): raise ValueError('Prefetch observation coverage')
    expected = []
    for p, observed in zip(predictions, observations):
        query = p['query']; ref = references[query]
        if ref['failed'] is not False or type(ref['gas']) is not int or ref['gas'] <= 0: raise ValueError('Vacuous EVM reference')
        actual, proposed = set(ref['slots']), set(p['slots'])
        if any(type(x) is not int or not 0 <= x < 2**256 for x in actual | proposed): raise ValueError('Slot encoding')
        identity = {k: ref[k] for k in ('gas', 'returnValue', 'failed', 'slots')}
        if observed != {'query': query, 'method': p['method'], **identity}:
            raise ValueError('Post-prefetch EVM observation changed')
        expected.append({'query': query, 'family': queries[query]['family'], 'method': p['method'],
            'predicted_slots': len(proposed), 'actual_slots': len(actual), 'hits': len(actual & proposed),
            'misses': len(actual - proposed), 'extra_slots': len(proposed - actual), 'exact_footprint': actual == proposed,
            'abstained': p['abstained'], 'candidates': p['candidates'], 'required_result_unchanged': True,
            'baseline_result_sha256': digest(identity)})
    if expected != report['results']: raise ValueError('Prediction/observed-footprint accounting')
    summary = {}
    for method in METHODS:
        rows = [r for r in expected if r['method'] == method]
        if len(rows) != 256: raise ValueError('Method coverage')
        summary[method] = {k: sum(int(r[k]) for r in rows) for k in ('hits', 'misses', 'extra_slots', 'predicted_slots', 'actual_slots', 'exact_footprint', 'abstained', 'candidates')}
    if report['summary'] != summary: raise ValueError('Summary mismatch')
    if (report['training_calls'], report['learned_trace_entries'], len(report['rejected_training']), report['heldout_queries'], report['retrieval_cases']) != (128, 96, 32, 256, 1280):
        raise ValueError('Corpus coverage')
    calls = report['local_rpc_calls']
    if calls['debug_traceCall'] != 128 + 256 * 2 + 1280 + 1: raise ValueError('Trace call accounting')
    if calls['eth_getStorageAt'] != sum(r['predicted_slots'] for r in expected) + calls['anvil_setStorageAt']:
        raise ValueError('Prefetch/readback accounting')
    if report['unique_recipes'] != len({x['recipe_sha256'] for x in corpus['parsed_recipes']}) or report['unique_recipes'] != 8:
        raise ValueError('Unique recipe accounting')
    if report['sealed_evaluation_rpc_calls'] != {'debug_traceCall': 1536, 'eth_getStorageAt': 834, 'eth_getBlockByNumber': 1}:
        raise ValueError('Read-only evaluation calls')
    if report['evaluation_phase_sealed'] is not True: raise ValueError('Evaluation phase was not sealed')
    readbacks = corpus['storage_readbacks']
    if len(readbacks) != calls['anvil_setStorageAt']: raise ValueError('Readback coverage')
    seen = set()
    for row in readbacks:
        key = row['owner'], row['slot']
        if key in seen: raise ValueError('Duplicate readback')
        seen.add(key)
        expected_value = '0x' + hashlib.sha256((row['owner'] + row['slot']).encode()).hexdigest()
        if row['expected'] != expected_value or row['observed'] != expected_value: raise ValueError('Fixture storage changed')
    if report['all_required_results_unchanged'] is not True or report['simulated_writes_not_committed'] is not True:
        raise ValueError('Runtime checks')
    return {**replay, 'retrieval_cases_accounted': len(expected),
            'keccak_mode': 'independent-cast-recompute' if with_cast else 'recorded-wire-fixtures',
            'scope': 'Offline reconstruction and saved-observation accounting. Default uses finite recorded Keccak replies, not independent cryptographic verification, a new EVM run or a cost benchmark.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', default=str(ROOT / 'data/evm-access-recipes.json'))
    p.add_argument('--report', default=str(ROOT / 'results/evm-access-recipes.json'))
    p.add_argument('--with-cast', action='store_true', help='Independently recompute every recorded Keccak using installed cast; no public RPC')
    a = p.parse_args()
    print(json.dumps(audit(json.loads(Path(a.corpus).read_text()), json.loads(Path(a.report).read_text()), with_cast=a.with_cast)))
