import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('swarm_topology', ROOT / 'scripts/benchmark_swarm_topology.py')
swarm = importlib.util.module_from_spec(spec); spec.loader.exec_module(swarm)


def key_for(owner, n, nonce=0):
    return next(swarm.digest(['test-key', i]) for i in range(nonce, nonce + 10000)
                if int(swarm.digest(['test-key', i])[:8], 16) % n == owner)


class NetworkTests(unittest.TestCase):
    def test_shortest_path_uses_region_cost_not_hop_count(self):
        net = swarm.RoleNetwork('weighted', [0, 1, 0, 0, 0], [(0, 1), (1, 4), (0, 2), (2, 3), (3, 4)])
        self.assertEqual(net.path(0, 4), (0, 2, 3, 4))
        self.assertEqual(net.path(0, 0), (0,))
        self.assertIsNone(net.path(4, 0))

    def test_invalid_networks_rejected(self):
        for regions, edges in [([0], []), ([0, True], []), ([0, 0], [(0, 0)]),
                               ([0, 0], [(0, 1), (0, 1)]), ([0, 0], [(0, 2)])]:
            with self.assertRaises(ValueError): swarm.RoleNetwork('bad', regions, edges)


class SimulationTests(unittest.TestCase):
    def test_flood_peer_hit_memoization_is_an_explicit_control(self):
        net = swarm.RoleNetwork('flood', [0, 0, 0], [(a, b) for a in range(3) for b in range(3) if a != b])
        key = key_for(0, 3)
        for policy, expected_messages in [('legacy', 12), ('matched', 8)]:
            sim = swarm.SwarmSimulation(net, 'flood', policy=policy)
            values = [sim.process_request(entry, key) for entry in (0, 1, 1)]
            self.assertEqual(len(set(values)), 1)
            self.assertEqual(sim.counts['origin_reads'], 1)
            self.assertEqual(sim.counts['peer_messages'], expected_messages)

    def test_all_cache_writes_are_bounded(self):
        net = swarm.RoleNetwork('all', [0, 0, 0], [(a, b) for a in range(3) for b in range(3) if a != b])
        for mode in ('isolated', 'flood', 'routed'):
            for policy in ('legacy', 'matched'):
                sim = swarm.SwarmSimulation(net, mode, cache_capacity=1, policy=policy)
                for i in range(12):
                    key = swarm.digest(['bounded', i])
                    sim.process_request(0, key); sim.process_request(1, key)
                self.assertLessEqual(sim.peak_entries, 1)
                self.assertTrue(all(len(cache) <= 1 for cache in sim.caches))

    def test_failure_prefix_is_charged_but_no_self_healing_is_invented(self):
        net = swarm.RoleNetwork('diamond', [0]*4, [(0, 1), (0, 2), (1, 3), (2, 3)])
        key = key_for(3, 4)
        self.assertEqual(net.path(0, 3), (0, 1, 3))
        for policy, expected_bytes in [('legacy', 0), ('matched', 128)]:
            sim = swarm.SwarmSimulation(net, policy=policy)
            sim.process_request(0, key, {1})
            self.assertEqual(sim.counts['failed_route_origin'], 1)
            self.assertEqual(sim.counts['peer_bytes'], expected_bytes)
            # A live 0->2->3 path exists but this model never repairs its path.
            self.assertEqual(sim.counts['successful_routes'], 0)

    def test_flood_failed_contacts_and_external_ingress_fallback(self):
        net = swarm.RoleNetwork('all', [0]*3, [(a, b) for a in range(3) for b in range(3) if a != b])
        sim = swarm.SwarmSimulation(net, 'flood')
        sim.process_request(0, key_for(2, 3), {1})
        self.assertEqual(sim.counts['peer_bytes'], 128*2 + 180)
        self.assertEqual(sim.counts['failed_contacts'], 1)
        sim.process_request(1, key_for(2, 3), {1})
        self.assertEqual(sim.counts['failed_ingress_origin'], 1)
        self.assertFalse(sim.caches[1])

    def test_lru_and_key_identity_do_not_return_an_old_token(self):
        net = swarm.RoleNetwork('pair', [0, 0], [(0, 1), (1, 0)])
        sim = swarm.SwarmSimulation(net, 'isolated', cache_capacity=2)
        keys = [swarm.digest(i) for i in range(3)]
        for index in (0, 1, 0, 2):
            self.assertEqual(sim.process_request(0, keys[index]), swarm.digest(['synthetic-value', keys[index]]))
        self.assertIn(keys[0], sim.caches[0]); self.assertNotIn(keys[1], sim.caches[0])
        sim.process_request(0, keys[1]); self.assertEqual(sim.counts['origin_reads'], 4)

    def test_invalid_request_and_cache_bounds(self):
        net = swarm.RoleNetwork('pair', [0, 0], [(0, 1), (1, 0)])
        with self.assertRaises(ValueError): swarm.SwarmSimulation(net, cache_capacity=0)
        with self.assertRaises(ValueError): swarm.SwarmSimulation(net, policy='unknown')
        sim = swarm.SwarmSimulation(net)
        for entry, key, failed in [(True, 'a'*64, ()), (0, 'bad', ()), (0, 'a'*64, [2])]:
            with self.assertRaises(ValueError): sim.process_request(entry, key, failed)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = json.loads((ROOT / 'results/swarm-topology-benchmark.json').read_text())
        cls.inputs = cls.saved['inputs']
        cls.replayed = swarm.run_benchmark(cls.inputs)

    def test_exact_report_replay_and_source_bindings(self):
        self.assertEqual(self.replayed, self.saved)
        lock = json.loads((ROOT / 'results/swarm-topology-protocol-lock.json').read_text())
        self.assertEqual(lock['input_sha256'], swarm.digest(self.inputs))
        for name, digest in lock['implementation_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)

    def test_input_corruption_and_omitted_historical_row_are_rejected(self):
        bad = copy.deepcopy(self.inputs); bad['aggregation']['edges'][0][2] += 1
        with self.assertRaises(ValueError): swarm.validate_inputs(bad)
        bad = copy.deepcopy(self.inputs); bad['legacy_rows'].pop()
        with self.assertRaisesRegex(ValueError, 'Pinned'): swarm.validate_inputs(bad)

    def test_scale_degrees_and_hop_comparison(self):
        p = self.saved['topology_profiles']; self.assertEqual(self.saved['roles'], 58)
        self.assertEqual(p['affinity']['directed_arcs'], 348)
        for name in ('ring', 'geographic', 'degree-rewired-11', 'degree-rewired-23'):
            self.assertEqual(p[name]['out_degrees'], p['affinity']['out_degrees'])
        for name in ('degree-rewired-11', 'degree-rewired-23'):
            self.assertEqual(p[name]['in_degrees'], p['affinity']['in_degrees'])
        self.assertEqual(round(p['affinity']['mean_hops_on_least_cost_routes'], 2), 3.61)
        self.assertEqual(round(p['ring']['mean_hops_on_least_cost_routes'], 2), 4.03)
        self.assertLess(p['geographic']['mean_hops_on_least_cost_routes'], p['affinity']['mean_hops_on_least_cost_routes'])

    def test_failure_claim_and_flood_negative_comparator_preserved(self):
        r = self.saved['historical_replay']; self.assertTrue(r['exact_ten_row_match'])
        self.assertEqual(r['original_failure_count'], 5)
        self.assertAlmostEqual(r['original_failure_percent'], 100*5/58, places=5)
        rows = {x['topology']: x for x in r['rows'][5:]}
        self.assertEqual(rows[swarm.LABELS['affinity']]['bsc_origin_reads'], 250)
        self.assertEqual(rows[swarm.LABELS['flood']]['bsc_origin_reads'], 193)
        self.assertEqual(rows[swarm.LABELS['affinity']]['bsc_system_cost_score_200'], 50000.18)
        self.assertFalse(self.saved['scope']['biological_self_healing_implemented'])
        self.assertTrue(all(x['coalesced_waiters'] == 0 for x in r['rows']))

    def test_complete_matched_matrix_and_negative_workloads(self):
        rows = self.saved['matched_policy_trials']; self.assertEqual(len(rows), 105)
        cases = {(r['seed'], len(r['failed_nodes']), r['topology']) for r in rows}
        self.assertEqual(len(cases), 105)
        for r in rows + self.saved['negative_controls']:
            m = r['metrics']; self.assertEqual(m['origin_reads'] + m['cache_hits'], 2048)
            self.assertTrue(r['all_fixture_values_match'])
            self.assertLessEqual(r['peak_cache_entries_per_role'], 16)
            self.assertEqual(r['coalesced_waiters'], 0)
        for seed in swarm.SEEDS:
            for failure_count in (0, 5, 6):
                group = [r for r in rows if r['seed']==seed and len(r['failed_nodes'])==failure_count]
                self.assertEqual(len(group), 7)
                self.assertEqual(len({tuple(r['failed_nodes']) for r in group}), 1)
                self.assertEqual(len({r['workload_sha256'] for r in group}), 1)
                self.assertEqual(len({r['outputs_sha256'] for r in group}), 1)
        unique = [r for r in self.saved['negative_controls'] if r['workload']=='unique-pinned']
        self.assertEqual(len(unique), 7)
        self.assertTrue(all(r['metrics']['origin_reads']==2048 for r in unique))
