from collections import Counter
import json
from pathlib import Path
import unittest
from synafly_lab.canonical import Invalid
from synafly_lab.model import Circuit, DEFAULT_MODEL
from synafly_lab.topology import Overlay, conventional_overlay, owners, rewire

GRAPH={'schema':'synafly.graph.v1','nodes':['1','2','3','4'],
       'edges':[[0,1,3],[1,2,2],[2,0,1],[2,3,5]]}

class TopologyTests(unittest.TestCase):
    def test_path_budget_cycles_and_failure_work(self):
        overlay=Overlay(GRAPH)
        result=overlay.search(0,[3],budget=3,hops=3)
        self.assertTrue(result['found']);self.assertEqual(result['contacts'],3);self.assertEqual(result['hops'],3)
        self.assertFalse(overlay.search(0,[3],budget=2,hops=3)['found'])
        self.assertFalse(overlay.search(0,[3],budget=3,hops=2)['found'])
        blocked=overlay.search(0,[3],failed=[1],budget=3,hops=3)
        self.assertEqual(blocked['contacts'],1);self.assertEqual(blocked['failed_contacts'],1)
    def test_replicas_and_direct_reference(self):
        overlay=Overlay(GRAPH)
        self.assertEqual(overlay.direct(0,[1,3],failed=[1])['contacts'],2)
        self.assertFalse(overlay.direct(0,[1,3],failed=[1,3])['found'])
        self.assertEqual(overlay.search(0,[0],budget=1,hops=1)['contacts'],0)
        self.assertTrue(overlay.search(0,[3],failed=[0],budget=3,hops=3)['entry_failed'])
    def test_owner_map_is_graph_independent_and_reproducible(self):
        self.assertEqual(owners('key',256),owners('key',256))
        self.assertEqual(len(set(owners('key',256))),3)
        self.assertNotEqual(owners('key',256),owners('other',256))
    def test_invalid_budgets_and_vertices_rejected(self):
        overlay=Overlay(GRAPH)
        for kwargs in [{'source':True,'targets':[1]}, {'source':0,'targets':[4]},
                       {'source':0,'targets':[]},{'source':0,'targets':[1],'budget':0}]:
            with self.assertRaises(Invalid):overlay.search(**kwargs)
    def test_rewiring_preserves_degree_and_outgoing_weights(self):
        graph=json.loads(Path('data/malecns-sample.json').read_text())
        changed,swaps=rewire(graph,42);self.assertGreater(swaps,0)
        def signature(g):
            return Counter(a for a,b,w in g['edges']),Counter(b for a,b,w in g['edges']),Counter((a,w) for a,b,w in g['edges'])
        self.assertEqual(signature(graph),signature(changed))
        self.assertEqual(len(graph['edges']),len(changed['edges']))
        self.assertNotEqual(graph,changed)
    def test_conventional_control_preserves_budget_not_biology(self):
        graph=json.loads(Path('data/malecns-sample.json').read_text())
        control=conventional_overlay(graph);Circuit(control,DEFAULT_MODEL)
        self.assertEqual(control['nodes'],graph['nodes']);self.assertEqual(len(control['edges']),605)
        connectivity=Overlay(control).connectivity()
        self.assertEqual(connectivity['reachable_ordered_pairs'],connectivity['possible_ordered_pairs'])
    def test_full_sample_is_not_silently_reduced_to_connected_component(self):
        graph=json.loads(Path('data/malecns-sample.json').read_text())
        overlay=Overlay(graph);info=overlay.connectivity()
        self.assertEqual(overlay.n,256)
        self.assertEqual(info['possible_ordered_pairs'],256*255)
        self.assertLess(info['reachable_ordered_pairs'],info['possible_ordered_pairs'])
