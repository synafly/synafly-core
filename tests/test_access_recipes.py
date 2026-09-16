"""Nine offline tests: recorded HTTP/Keccak replies, no Anvil, cast or public RPC."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from synafly_lab import access_recipes as recipes
from synafly_lab.access_recipes import (UnsupportedTrace, call_context, extract, evaluate,
                                      footprint, materialize, word)
from synafly_lab.fly_recipe_index import RecipeIndex, METHODS, features
from synafly_lab.recipe_fixtures import calldata
from synafly_lab.recipe_node import RecipeNode
from synafly_lab import recipe_node

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from demo_access_recipes import request, context
from verify_access_recipes import audit, recorded_keccak_provider


class Wire:
    """Replay only exact recorded trace requests, through the actual client codec."""
    def __init__(self, corpus):
        options = {'enableMemory': True, 'disableStorage': True}
        self.traces = {recipes.canonical([request(row), 'latest', options]): row['trace']
                       for row in corpus['training']}
        extra = corpus['wire_examples']['dynamic_jump']
        self.traces[recipes.canonical([extra['request'], 'latest', options])] = extra['trace']
        self.calls = []; self.forced_response = None

    def connection(self, host, port, timeout):
        if host != '127.0.0.1': raise AssertionError('Not loopback')
        wire = self
        class Connection:
            def request(self, method, path, body, headers):
                if method != 'POST' or path != '/': raise AssertionError('Unexpected transport')
                self.query = json.loads(body); wire.calls.append(self.query)
            def getresponse(self):
                if self.query['method'] != 'debug_traceCall': raise AssertionError('Unrecorded RPC method')
                result = wire.traces[recipes.canonical(self.query['params'])]
                body = wire.forced_response or {'jsonrpc': '2.0', 'id': self.query['id'], 'result': result}
                raw = json.dumps(body).encode()
                return SimpleNamespace(status=200, read=lambda n: raw[:n])
            def close(self): pass
        return Connection()


class RecordedFixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads((ROOT / 'data/evm-access-recipes.json').read_text())
        patches = [patch.object(recipes, 'keccak', new=recorded_keccak_provider(cls.corpus)),
                   patch('subprocess.run', side_effect=AssertionError('Offline tests must not run cast')),
                   patch('subprocess.Popen', side_effect=AssertionError('Offline tests must not start Anvil'))]
        for item in patches:
            item.start(); cls.addClassCleanup(item.stop)


class ExpressionTests(RecordedFixtureCase):
    def test_ethereum_keccak_not_sha3_and_argument_padding(self):
        self.assertEqual(hex(recipes.keccak(b'')), '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')
        self.assertNotEqual(word(recipes.keccak(b'')), hashlib.sha3_256(b'').digest())
        ctx = call_context(b'\x01', 7)
        self.assertEqual(evaluate(('arg', 0), ctx), 1 << 248)
        self.assertEqual(evaluate(('caller',), ctx), 7)
        with self.assertRaises(UnsupportedTrace): evaluate(('unknown',), ctx)
        with self.assertRaisesRegex(ValueError, 'Unrecorded'): recipes.keccak(b'not-in-the-recorded-wire-fixture')

    def test_expression_depth_work_and_input_bounds(self):
        expr = ('const', 1)
        for _ in range(10): expr = ('add', expr, expr)
        with self.assertRaises(UnsupportedTrace): evaluate(expr, call_context(b'', 0))
        with self.assertRaises(ValueError): call_context(b'x' * 257, 0)
        with self.assertRaises(ValueError): call_context(b'', True)
        with self.assertRaises(UnsupportedTrace): evaluate(('arg', -1), call_context(b'', 0))


class RecordedTraceTests(RecordedFixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass(); cls.wire = Wire(cls.corpus)
        cls.node = RecipeNode(); cls.node.port = 1; cls.node.sealed = True
        cls.node.process = SimpleNamespace(poll=lambda: None)
        item = patch.object(recipe_node.http.client, 'HTTPConnection', side_effect=cls.wire.connection)
        item.start(); cls.addClassCleanup(item.stop)
        cls.rows = {(r['family'], r['serial']): r for r in cls.corpus['training']}
        cls.samples = {}
        for name in ('direct', 'nested', 'caller', 'write', 'branch', 'unsupported', 'state-dependent'):
            row = cls.rows[name, 0]
            cls.samples[name] = bytes.fromhex(row['code'][2:]), cls.node.trace(request(row)), context(row)

    def test_parameterized_slots_match_recorded_execution(self):
        for name in ('direct', 'nested', 'caller', 'write', 'branch'):
            code, trace, ctx = self.samples[name]; recipe = extract(code, trace, ctx)
            self.assertEqual(materialize(recipe, ctx), footprint(trace))
            row = self.rows[name, 4]; actual = self.node.trace(request(row))
            self.assertEqual(materialize(recipe, context(row)), footprint(actual))
            self.assertNotEqual(footprint(trace), footprint(actual))

    def test_branch_guard_rejects_different_path(self):
        code, trace, ctx = self.samples['branch']; recipe = extract(code, trace, ctx)
        self.assertIsNone(materialize(recipe, call_context(calldata(100, 200, 3), ctx['caller'])))

    def test_unsupported_memory_and_state_dependent_keys_abstain(self):
        for name in ('unsupported', 'state-dependent'):
            code, trace, ctx = self.samples[name]
            with self.assertRaises(UnsupportedTrace): extract(code, trace, ctx)

    def test_calldata_computed_jump_destination_abstains(self):
        row = self.corpus['wire_examples']['dynamic_jump']
        trace = self.node.trace(row['request']); ctx = call_context(bytes.fromhex(row['request']['data'][2:]), row['caller'])
        self.assertEqual(footprint(trace), {1})
        with self.assertRaisesRegex(UnsupportedTrace, 'Dynamic'):
            extract(bytes.fromhex(row['code'][2:]), trace, ctx)

    def test_malformed_stack_memory_opcode_and_frame_rejected(self):
        code, trace, ctx = self.samples['direct']; variants = []
        t = copy.deepcopy(trace); t['structLogs'][1]['stack'] = []; variants.append(t)
        t = copy.deepcopy(trace); next(x for x in t['structLogs'] if x['op'] in {'KECCAK256', 'SHA3'})['memory'][0] = '0x0'; variants.append(t)
        t = copy.deepcopy(trace); t['structLogs'][0]['op'] = 'STOP'; variants.append(t)
        t = copy.deepcopy(trace); t['structLogs'][0]['depth'] = 2; variants.append(t)
        variants.append({**trace, 'structLogs': trace['structLogs'][:-1]})
        for bad in variants:
            with self.assertRaises(UnsupportedTrace): extract(code, bad, ctx)
        self.wire.forced_response = {'jsonrpc': '2.0', 'id': 99, 'result': trace}
        try:
            with self.assertRaises(ValueError): self.node.trace(request(self.rows['direct', 0]))
        finally: self.wire.forced_response = None

    def test_frozen_catalog_code_binding_and_retrieval(self):
        code, trace, ctx = self.samples['direct']; recipe = extract(code, trace, ctx)
        index = RecipeIndex(); h = hex(recipes.keccak(code)); selector = ctx['data'][:4].hex()
        index.add(h, selector, ctx, recipe, footprint(trace)); index.frozen = True; before = index.fingerprint()
        # Same route descriptor, new mapping argument; hash answer is in the captured corpus.
        query = call_context(calldata(10000, 20000, 0), 0xc0000000)
        for method in METHODS:
            result = index.query(method, h, selector, query)
            if method != 'stale-slots': self.assertEqual(set(result['slots']), materialize(recipe, query))
            self.assertTrue(index.query(method, 'changed-code', selector, query)['abstained'])
        with self.assertRaises(ValueError): index.add(h, selector, ctx, recipe, set())
        self.assertEqual(index.fingerprint(), before)
        self.assertEqual(len(index.tag(features(ctx))), 16)
        calls = len(self.wire.calls)
        for method in ('eth_sendTransaction', 'eth_sendRawTransaction', 'anvil_setStorageAt', 'anvil_setCode', 'evm_mine'):
            with self.assertRaises(ValueError): self.node.call(method, [])
        self.assertEqual(len(self.wire.calls), calls)


class SavedRecipeEvidenceTests(unittest.TestCase):
    def test_saved_corpus_and_all_retrieval_cases_replay(self):
        corpus = json.loads((ROOT / 'data/evm-access-recipes.json').read_text())
        report = json.loads((ROOT / 'results/evm-access-recipes.json').read_text())
        with patch('subprocess.run', side_effect=AssertionError('No cast in offline audit')), \
                patch('subprocess.Popen', side_effect=AssertionError('No Anvil in offline audit')):
            result = audit(corpus, report)
            self.assertEqual(result['retrieval_cases_accounted'], 1280)
            self.assertEqual(result['keccak_mode'], 'recorded-wire-fixtures')
            changed = copy.deepcopy(report); changed['summary']['flyhash']['hits'] += 1
            with self.assertRaisesRegex(ValueError, 'Summary'): audit(corpus, changed)
            changed_corpus = copy.deepcopy(corpus); changed_corpus['prefetch_observations'][0]['gas'] += 1
            changed = copy.deepcopy(report); changed['corpus_sha256'] = recipes.digest(changed_corpus)
            with self.assertRaisesRegex(ValueError, 'Post-prefetch'): audit(changed_corpus, changed)
