#!/usr/bin/env python3
"""Real local EVM traces, causal symbolic access recipes and retrieval controls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synafly_lab import access_recipes as recipes
from synafly_lab.access_recipes import (UnsupportedTrace, call_context, canonical, digest,
                                      extract, footprint, materialize, word)
from synafly_lab.fly_recipe_index import METHODS, RecipeIndex
from synafly_lab.recipe_fixtures import SELECTOR, calldata, fixture_codes, owner
from synafly_lab.recipe_node import RecipeNode

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['scripts/demo_access_recipes.py', 'synafly_lab/access_recipes.py',
           'synafly_lab/fly_recipe_index.py', 'synafly_lab/recipe_fixtures.py',
           'synafly_lab/recipe_node.py',
           'synafly_lab/rpc.py', 'scripts/verify_access_recipes.py', 'docs/evm-access-recipes-design.md']


def bindings(): return {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in SOURCES}
def hashcode(code): return '0x' + word(recipes.keccak(code)).hex()
def hexslot(value): return '0x' + word(value).hex()
def context(row): return call_context(bytes.fromhex(row['data'][2:]), int(row['caller'], 16))
def request(row): return {'to': row['owner'], 'from': row['caller'], 'data': row['data'], 'value': '0x0', 'gas': '0x30d40'}
def result_identity(trace): return {'gas': trace['gas'], 'returnValue': trace['returnValue'], 'failed': trace['failed'], 'slots': sorted(footprint(trace))}


def make_row(family, index, serial, training):
    first = 100 + serial if training else 10000 + serial
    second = 200 + serial if training else 20000 + serial
    route = serial if training else 16 + serial
    caller = 0xb0000000 + serial if training else 0xc0000000 + serial
    return {'family': family, 'owner': owner(index), 'data': '0x' + calldata(first, second, route).hex(),
            'caller': '0x' + f'{caller:040x}', 'serial': serial}


def build_catalog(training):
    catalog = RecipeIndex(); rejected = []; parsed = []
    for number, row in enumerate(training):
        code = bytes.fromhex(row['code'][2:])
        if hashcode(code) != row['code_hash']: raise ValueError('Code identity')
        try:
            recipe = extract(code, row['trace'], context(row))
            actual = footprint(row['trace'])
            if materialize(recipe, context(row)) != actual: raise ValueError('Training reconstruction failed')
            catalog.add(row['code_hash'], row['data'][2:10], context(row), recipe, actual)
            parsed.append({'training_row': number, 'recipe': recipe, 'recipe_sha256': digest(recipe)})
        except UnsupportedTrace as exc:
            rejected.append({'training_row': number, 'family': row['family'], 'reason': str(exc)})
    catalog.frozen = True
    return catalog, parsed, rejected


def _experiment():
    codes = fixture_codes(); training = []; inputs = []; predictions = []; references = []; results = []
    observations = []; readbacks = []
    with RecipeNode() as node:
        # A small extra actual trace becomes the offline dynamic-jump rejection fixture.
        jump_code = bytes.fromhex('600035565b600154005b60025400')
        jump_request = {'to': owner(20), 'data': '0x' + word(4).hex(), 'gas': '0x30d40'}
        node.call('anvil_setCode', [owner(20), '0x' + jump_code.hex()])
        wire_example = {'code': '0x' + jump_code.hex(), 'request': jump_request,
                        'trace': node.trace(jump_request), 'caller': 0}
        # Independent Keccak vectors: cast versus the client's web3_sha3.
        rng = random.Random(410)
        vectors = [b'', b'abc'] + [rng.randbytes(n) for n in (1, 31, 32, 63, 64, 96, 128)]
        for data in vectors:
            if node.call('web3_sha3', ['0x' + data.hex()]) != hexslot(recipes.keccak(data)): raise ValueError('Keccak provider mismatch')
        for index, family in enumerate(codes):
            code = codes['direct'] if family == 'upgrade' else codes[family]
            node.call('anvil_setCode', [owner(index), '0x' + code.hex()])
            for serial in range(16):
                row = make_row(family, index, serial, True)
                row.update(code='0x' + code.hex(), code_hash=hashcode(code), trace=node.trace(request(row)))
                training.append(row)
        catalog, parsed, rejected = build_catalog(training); frozen = catalog.fingerprint()
        # Same address, changed bytecode: old code's recipes must not be used.
        upgrade_index = list(codes).index('upgrade')
        node.call('anvil_setCode', [owner(upgrade_index), '0x' + codes['upgrade'].hex()])
        for index, family in enumerate(codes):
            observed = node.call('eth_getCode', [owner(index), 'latest'])
            if observed != '0x' + codes[family].hex(): raise ValueError('Held-out code mismatch')
            for serial in range(32):
                row = make_row(family, index, serial, False); row['code_hash'] = hashcode(codes[family])
                inputs.append(row)
                for method in METHODS:
                    prediction = catalog.query(method, row['code_hash'], row['data'][2:10], context(row))
                    predictions.append({'query': len(inputs) - 1, 'method': method, **prediction})
        predictions_hash = digest(predictions)
        # Only after all predictions are frozen may held-out traces be inspected.
        seeds = {}
        for row in inputs:
            trace = node.trace(request(row))
            for slot in footprint(trace):
                key = row['owner'], slot
                seeds[key] = hashlib.sha256((row['owner'] + hexslot(slot)).encode()).digest()
        for (address, slot), value in sorted(seeds.items()):
            node.call('anvil_setStorageAt', [address, hexslot(slot), '0x' + value.hex()])
        block = node.seal(); block_number = block['number']
        before_evaluation_calls = dict(node.calls)
        for query, row in enumerate(inputs):
            baseline = node.trace(request(row), block_number); identity = result_identity(baseline)
            reference = {'query': query, **identity}; references.append(reference)
            for prediction in predictions[query * len(METHODS):(query + 1) * len(METHODS)]:
                for slot in prediction['slots']:
                    node.call('eth_getStorageAt', [row['owner'], hexslot(slot), block_number])
                after = node.trace(request(row), block_number)
                observed_identity = result_identity(after)
                observations.append({'query': query, 'method': prediction['method'], **observed_identity})
                if observed_identity != identity: raise ValueError('Prefetch changed EVM result/gas/accesses')
                actual, predicted = set(identity['slots']), set(prediction['slots'])
                results.append({'query': query, 'family': row['family'], 'method': prediction['method'],
                    'predicted_slots': len(predicted), 'actual_slots': len(actual), 'hits': len(actual & predicted),
                    'misses': len(actual - predicted), 'extra_slots': len(predicted - actual),
                    'exact_footprint': actual == predicted, 'abstained': prediction['abstained'],
                    'candidates': prediction['candidates'], 'required_result_unchanged': True,
                    'baseline_result_sha256': digest(identity)})
        for (address, slot), value in seeds.items():
            actual = node.call('eth_getStorageAt', [address, hexslot(slot), 'latest'])
            readbacks.append({'owner': address, 'slot': hexslot(slot), 'expected': '0x' + value.hex(), 'observed': actual})
            if actual != '0x' + value.hex(): raise ValueError('Trace calls committed state writes')
        final = node.call('eth_getBlockByNumber', ['latest', False])
        if final['hash'] != block['hash'] or catalog.fingerprint() != frozen or digest(predictions) != predictions_hash:
            raise ValueError('State/catalog/prediction mutation')
        calls = dict(node.calls)
        evaluation_calls = {k: v - before_evaluation_calls.get(k, 0) for k, v in calls.items() if v - before_evaluation_calls.get(k, 0)}
    corpus = {'schema': 'synafly.access-recipes-corpus.v1', 'kind': 'synthetic_bytecode_actual_local_evm_training_traces', 'training': training,
              'heldout_inputs': inputs, 'parsed_recipes': parsed, 'rejected_training': rejected,
              'catalog_sha256': frozen, 'frozen_predictions': predictions, 'predictions_sha256': predictions_hash,
              'heldout_reference_results': references, 'prefetch_observations': observations,
              'storage_readbacks': readbacks, 'wire_examples': {'dynamic_jump': wire_example}}
    summary = {}
    for method in METHODS:
        rows = [r for r in results if r['method'] == method]
        summary[method] = {k: sum(int(r[k]) for r in rows) for k in ('hits', 'misses', 'extra_slots', 'predicted_slots', 'actual_slots', 'exact_footprint', 'abstained', 'candidates')}
    return corpus, {'schema': 'synafly.access-recipes-report.v1', 'status': 'passed', 'experiment': 'causal-evm-access-recipe-retrieval-v1',
        'kind': 'actual_evm_traces_synthetic_contracts_not_bsc_native_cost',
        'training_calls': len(training), 'learned_trace_entries': len(parsed), 'rejected_training': rejected,
        'heldout_queries': len(inputs), 'retrieval_cases': len(results), 'summary': summary, 'results': results,
        'catalog_sha256': frozen, 'predictions_sha256': predictions_hash, 'corpus_sha256': digest(corpus),
        'local_rpc_calls': calls, 'sealed_evaluation_rpc_calls': evaluation_calls,
        'extra_wire_fixture_calls': {'anvil_setCode': 1, 'debug_traceCall': 1},
        'evaluation_phase_sealed': node.sealed,
        'local_state_context': {'chain_id': 1337, 'block_number': block_number, 'block_hash': block['hash'], 'state_root': block['stateRoot']},
        'keccak_crosscheck_vectors': len(vectors), 'all_required_results_unchanged': True, 'simulated_writes_not_committed': True,
        'public_rpc_calls': 0, 'public_transactions': 0,
        'unique_recipes': len({row['recipe_sha256'] for row in parsed}),
        'limits': ['Authored bytecode and call-schema fixtures, not arbitrary Solidity coverage or real traffic.',
                   'Hints identify slots, never values; unsupported traces and changed code abstain.',
                   'No native BSC inline-prefetch/CPU/physical-I/O/net-cost benchmark.',
                   'Subprocess-backed cast Keccak is a correctness provider, not production inference performance.',
                   'Guarded dictionary is a required strong baseline; template reuse alone is not FlyHash superiority.'],
        'implementation_sha256': bindings()}


def experiment():
    """Record exact hash answers for optional finite-fixture offline replay."""
    original = recipes.keccak; vectors = {}
    def recording(data):
        value = original(data); vectors['0x' + data.hex()] = hexslot(value); return value
    # Single-threaded research runner only; never install a mock in a public service.
    with patch.object(recipes, 'keccak', new=recording):
        corpus, report = _experiment()
    corpus['keccak_wire_fixtures'] = [{'input': data, 'output': value} for data, value in sorted(vectors.items())]
    report['keccak_fixture_count'] = len(vectors)
    report['corpus_sha256'] = digest(corpus)
    return corpus, report


def verify(corpus, report):
    if corpus.get('schema') != 'synafly.access-recipes-corpus.v1' or report.get('schema') != 'synafly.access-recipes-report.v1':
        raise ValueError('Recipe corpus/report schema')
    if report['status'] != 'passed' or report['implementation_sha256'] != bindings() or digest(corpus) != report['corpus_sha256']:
        raise ValueError('Source/corpus binding')
    catalog, recipes, rejected = build_catalog(corpus['training'])
    if catalog.fingerprint() != report['catalog_sha256'] or digest(recipes) != digest(corpus['parsed_recipes']) or rejected != report['rejected_training']:
        raise ValueError('Trace recipe reconstruction')
    predictions = []
    for query, row in enumerate(corpus['heldout_inputs']):
        for method in METHODS:
            predictions.append({'query': query, 'method': method, **catalog.query(method, row['code_hash'], row['data'][2:10], context(row))})
    if digest(predictions) != report['predictions_sha256'] or predictions != corpus['frozen_predictions']:
        raise ValueError('Held-out prediction replay')
    return {'training_traces_replayed': len(corpus['training']), 'heldout_predictions_replayed': len(predictions), 'public_network_used': False}


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='results/evm-access-recipes.json')
    p.add_argument('--corpus', default='data/evm-access-recipes.json'); p.add_argument('--verify', action='store_true')
    a = p.parse_args()
    if a.verify:
        from verify_access_recipes import audit
        print(json.dumps(audit(json.loads(Path(a.corpus).read_text()), json.loads(Path(a.out).read_text()))))
    else:
        write(a.out, {'status': 'running', 'public_rpc_calls': 0})
        try:
            corpus, report = experiment(); write(a.corpus, corpus); write(a.out, report)
        except BaseException as exc:
            write(a.out, {'status': 'failed', 'failure_type': type(exc).__name__, 'public_rpc_calls': 0}); raise
        print(json.dumps({'training_traces': report['training_calls'], 'heldout_queries': report['heldout_queries'],
                          'retrieval_cases': report['retrieval_cases'], 'all_required_results_unchanged': True, 'summary': report['summary']}))
