import copy,json,tempfile,unittest
from pathlib import Path
from synafly_lab.canonical import Invalid,canonical,parse,digest
from synafly_lab.checkpoint import Run
from synafly_lab.model import Circuit,DEFAULT_MODEL,stimulus
from synafly_lab.store import Store,Conflict

GRAPH={'schema':'synafly.graph.v1','nodes':['1','2','3'],'edges':[[0,1,200],[1,2,200],[2,0,200]]}
def env(p):return {'hash':digest(p),'checkpoint':p}

class CoreTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.run=Run(GRAPH);self.store=Store(Path(self.tmp.name)/'state.sqlite',self.run)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def frames(self,start,n):return [stimulus(self.run.spec['seed'],i,3) for i in range(start,start+n)]
    def test_strict_canonical_json(self):
        for raw in ['{"a":1,"a":2}','{"a":NaN}','{"a":1.0}','{"a":true}']:
            with self.assertRaises(Invalid):parse(raw)
        self.assertEqual(canonical({'z':1,'a':2}),b'{"a":2,"z":1}')
    def test_replay_resume_exact(self):
        direct=self.run.circuit.initial()
        for f in self.frames(0,32):direct=self.run.circuit.step(direct,f)
        head=self.store.head()
        for start in range(0,32,8):head=self.store.advance(self.frames(start,8),head['hash'])
        self.assertEqual(head['checkpoint']['state'],direct)
        self.store.close();self.store=Store(Path(self.tmp.name)/'state.sqlite',self.run)
        self.assertEqual(self.store.head(),head);self.assertEqual(self.store.verify_history(),5)
    def test_tamper_rehash_does_not_fool_replay(self):
        p=self.run.advance(self.run.genesis(),self.frames(0,8));p['state']['potential'][0]+=1;p['state_hash']=digest(p['state'])
        with self.assertRaises(Invalid):self.store.import_chain([env(p)])
        self.assertEqual(self.store.head()['checkpoint']['sequence'],0)
    def test_graph_and_model_substitution(self):
        for key in ['graph_hash','model_hash','run_id']:
            p=self.run.advance(self.run.genesis(),self.frames(0,1));p[key]='f'*64
            with self.assertRaises(Invalid):self.store.import_chain([env(p)])
    def test_partial_import_rolls_back(self):
        one=self.run.advance(self.run.genesis(),self.frames(0,2));two=self.run.advance(one,self.frames(2,2));two['tick']+=1
        with self.assertRaises(Invalid):self.store.import_chain([env(one),env(two)])
        self.assertFalse(self.store.contains(digest(one)));self.assertEqual(self.store.head()['checkpoint']['sequence'],0)
    def test_duplicate_is_idempotent(self):
        p=self.run.advance(self.run.genesis(),self.frames(0,2));self.store.import_chain([env(p)])
        result=self.store.import_chain([env(p)]);self.assertEqual(result['imported'],0);self.assertEqual(self.store.verify_history(),2)
    def test_forks_and_stale_writes_do_not_replace_head(self):
        original=self.store.head();good=self.store.advance([[[0,1400]]],original['hash']);fork=self.run.advance(original['checkpoint'],[[[1,1400]]])
        with self.assertRaises(Conflict):self.store.import_chain([env(fork)])
        with self.assertRaises(Conflict):self.store.advance([[]],original['hash'])
        self.assertEqual(self.store.head(),good)
    def test_inputs_bounded_and_typed(self):
        for frames in [[],[[]]*17,[[[True,2]]],[[[0,1],[0,2]]],[[[4,0]]],[[[0,-1]]]]:
            with self.assertRaises(Invalid):self.run.advance(self.run.genesis(),frames)
    def test_topology_affects_real_state(self):
        no_edges=copy.deepcopy(GRAPH);no_edges['edges']=[];other=Run(no_edges)
        inputs=[[[0,1400]]]+[[]]*4
        self.assertNotEqual(self.run.advance(self.run.genesis(),inputs)['state'],other.advance(other.genesis(),inputs)['state'])
    def test_database_wrong_run_rejected(self):
        with self.assertRaises(Invalid):Store(Path(self.tmp.name)/'state.sqlite',Run(GRAPH,'different'))
    def test_corrupted_storage_detected(self):
        h=self.store.head()['hash'];self.store.db.execute('UPDATE objects SET payload=? WHERE hash=?',(b'{}',h))
        with self.assertRaises(Invalid):self.store.get(h)
    def test_real_sample_provenance(self):
        raw=Path('data/malecns-sample.json').read_bytes();meta=json.loads(Path('data/provenance.json').read_text())
        import hashlib
        self.assertEqual(hashlib.sha256(raw).hexdigest(),meta['sample_file_sha256'])
        c=Circuit(parse(raw),DEFAULT_MODEL);self.assertEqual(c.n,256);self.assertEqual(len(c.graph['edges']),605)
