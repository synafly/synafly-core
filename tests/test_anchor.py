import copy,unittest
from synafly_lab.anchor import ReadOnlyRPC,commitment,word,EVENT_TOPIC,commit_calldata
from synafly_lab.canonical import Invalid
from synafly_lab.checkpoint import Run
from test_core import GRAPH

class FakeRPC(ReadOnlyRPC):
    def __init__(self):
        super().__init__('http://127.0.0.1:8545',97);self.chain='0x61';self.height='0xc';self.contract='0x'+'a'*40;self.tx='0x'+'b'*64;self.block='0x'+'c'*64
        self.c=commitment(Run(GRAPH).genesis())
        self.receipt={'status':'0x1','transactionHash':self.tx,'to':self.contract,'blockNumber':'0xa','blockHash':self.block,'logs':[{'address':self.contract,'topics':[EVENT_TOPIC,'0x'+self.c['lineage']],'data':'0x'+word(0)+word(0)+''.join(self.c[k] for k in ['graph','model','parent','checkpoint'])}]}
    def _request(self,method,params):
        if method=='eth_chainId':return self.chain
        if method=='eth_getTransactionReceipt':return self.receipt
        if method=='eth_getBlockByNumber':return {'hash':self.block}
        if method=='eth_blockNumber':return self.height
        if method=='eth_getCode':return '0x6000'
        if method=='eth_call':return '0x'+'d'*64
        raise AssertionError('unexpected method')

class AnchorTests(unittest.TestCase):
    def test_adapter_cannot_broadcast_or_sign(self):
        r=FakeRPC()
        for method in ['eth_sendTransaction','eth_sendRawTransaction','eth_sign','personal_sign']:
            with self.assertRaises(Invalid):r.call(method,[])
    def test_receipt_matches_exact_event(self):
        r=FakeRPC();self.assertEqual(r.verify(r.tx,r.contract,r.c)['checkpoint_hash'],r.c['checkpoint'])
    def test_wrong_chain_rejected(self):
        r=FakeRPC();r.chain='0x38'
        with self.assertRaises(Invalid):r.verify(r.tx,r.contract,r.c)
    def test_failed_receipt_rejected(self):
        r=FakeRPC();r.receipt['status']='0x0'
        with self.assertRaises(Invalid):r.verify(r.tx,r.contract,r.c)
    def test_event_substitution_rejected(self):
        r=FakeRPC();r.receipt['logs'][0]['data']='0x'+word(0)*6
        with self.assertRaises(Invalid):r.verify(r.tx,r.contract,r.c)
    def test_reorg_rejected(self):
        r=FakeRPC();r.block='0x'+'e'*64
        with self.assertRaises(Invalid):r.verify(r.tx,r.contract,r.c)
    def test_confirmation_depth(self):
        r=FakeRPC();r.height='0xa'
        with self.assertRaises(Invalid):r.verify(r.tx,r.contract,r.c,2)
    def test_signatures_are_length_checked(self):
        r=FakeRPC()
        with self.assertRaises(Invalid):commit_calldata(r.c,['0x12','0x34'])
