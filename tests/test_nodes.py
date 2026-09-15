import json,secrets,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from synafly_lab.canonical import Invalid
from synafly_lab.checkpoint import Run
from synafly_lab.node import serve
from synafly_lab.peer import origin,sync
from synafly_lab.store import Store
from test_core import GRAPH

class NodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'s.sqlite',Run(GRAPH));self.token=secrets.token_hex(24)
        self.server=serve(self.store,'test',{},self.token);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.store.close();self.tmp.cleanup()
    def post(self,path,body,authorized=True):
        headers={'Content-Type':'application/json'}
        if authorized:headers['Authorization']='Bearer '+self.token
        r=urllib.request.Request(self.base+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with urllib.request.urlopen(r) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as e:
            try:return e.code,json.load(e)
            finally:e.close()
    def test_operator_required_and_arbitrary_peer_forbidden(self):
        self.assertEqual(self.post('/v1/advance',{},False)[0],401)
        self.assertEqual(self.post('/v1/sync',{'peer':'http://169.254.169.254'})[0],400)
        self.assertEqual(self.store.head()['checkpoint']['sequence'],0)
    def test_authenticated_advance_and_race_guard(self):
        h=self.store.head()['hash'];body={'expected_parent':h,'inputs':[[[0,1400]]]}
        self.assertEqual(self.post('/v1/advance',body)[0],200);self.assertEqual(self.post('/v1/advance',body)[0],409)
    def test_peer_origin_boundary(self):
        for value in ['http://10.0.0.1','https://example.com/path','https://user:pass@example.com','file:///tmp/a','https://example.com#fragment']:
            with self.assertRaises(Invalid):origin(value)
    def test_real_peer_pull_and_stale_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipient=Store(Path(tmp)/'r.sqlite',Run(GRAPH))
            try:
                h=self.store.head();self.store.advance([[[0,1400]]],h['hash']);sync(recipient,self.base)
                self.assertEqual(recipient.head(),self.store.head())
                h=recipient.head();recipient.advance([[]],h['hash'])
                with self.assertRaises(Invalid):sync(recipient,self.base)
                self.assertEqual(recipient.head()['checkpoint']['sequence'],2)
            finally:recipient.close()
