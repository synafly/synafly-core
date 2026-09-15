"""BSC-compatible ABI preparation and read-only receipt checks. No public-chain signer."""
import json
import re
import urllib.request
import urllib.error
from .peer import NoRedirect
from urllib.parse import urlsplit
from .canonical import Invalid,digest

MESSAGE_SELECTOR='65a7fea9'
COMMIT_SELECTOR='de1046e7'
EVENT_TOPIC='0xe7c2fb10036eb4e3a191f67d07d52241c5fc608fc85e34bd48dfeaac5b59b5b3'
READ_METHODS={'eth_chainId','eth_call','eth_getCode','eth_getTransactionReceipt','eth_getBlockByNumber','eth_blockNumber'}

def hexbytes(value,n):
    if type(value) is not str or not re.fullmatch('0x[0-9a-fA-F]{'+str(n*2)+'}',value):raise Invalid('Invalid hex field')
    return value[2:].lower()

def word(n):
    if type(n) is not int or not 0<=n<2**256:raise Invalid('ABI integer')
    return f'{n:064x}'

def commitment(payload):
    return {'lineage':payload['run_id'],'graph':payload['graph_hash'],'model':payload['model_hash'],'checkpoint':digest(payload),'parent':payload['parent'],'sequence':payload['sequence'],'tick':payload['tick']}

def tuple_data(c):
    hashes=''.join(hexbytes('0x'+c[k],32) for k in ['lineage','graph','model','checkpoint','parent'])
    for k in ['sequence','tick']:
        if type(c[k]) is not int or not 0<=c[k]<2**64:raise Invalid('ABI uint64')
    return hashes+word(c['sequence'])+word(c['tick'])

def message_calldata(c):return '0x'+MESSAGE_SELECTOR+tuple_data(c)

def commit_calldata(c,signatures):
    if not 2<=len(signatures)<=16:raise Invalid('Signature count')
    items=[word(65)+hexbytes(s,65).ljust(192,'0') for s in signatures]
    offsets=[];cursor=32*len(items)
    for item in items:offsets.append(word(cursor));cursor+=len(item)//2
    return '0x'+COMMIT_SELECTOR+tuple_data(c)+word(8*32)+word(len(items))+''.join(offsets+items)

class ReadOnlyRPC:
    def __init__(self,url,expected_chain):
        u=urlsplit(url)
        if u.username or u.password or u.fragment or u.scheme not in {'https','http'}:raise Invalid('RPC URL')
        if u.scheme=='http' and u.hostname not in {'127.0.0.1','localhost','::1'}:raise Invalid('Remote RPC requires HTTPS')
        if expected_chain not in {56,97}:raise Invalid('Expected BSC chain ID 56 or 97')
        self.url=url;self.expected_chain=expected_chain
    def _request(self,method,params):
        request=urllib.request.Request(self.url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as r:
                raw=r.read(1024*1024+1)
                if len(raw)>1024*1024:raise Invalid('RPC response limit')
                result=json.loads(raw)
        except urllib.error.HTTPError as e:
            e.close();raise Invalid('RPC HTTP error') from None
        except (urllib.error.URLError,TimeoutError,ValueError):raise Invalid('RPC unavailable or malformed response') from None
        if type(result) is not dict or result.get('id')!=1 or result.get('jsonrpc')!='2.0':raise Invalid('RPC envelope mismatch')
        if 'error' in result:raise Invalid('RPC rejected '+method)
        return result.get('result')
    def call(self,method,params):
        if method not in READ_METHODS:raise Invalid('Public adapter is read-only')
        return self._request(method,params)
    def check_chain(self):
        if int(self.call('eth_chainId',[]),16)!=self.expected_chain:raise Invalid('Wrong RPC chain')
    def prepare(self,contract,c):
        self.check_chain();hexbytes(contract,20)
        if self.call('eth_getCode',[contract,'latest'])=='0x':raise Invalid('No registry code')
        message=self.call('eth_call',[{'to':contract,'data':message_calldata(c)},'latest']);hexbytes(message,32)
        return {'chain_id':self.expected_chain,'contract':contract,'commitment':c,'personal_sign_message':message,'public_broadcast_performed':False}
    def verify(self,tx_hash,contract,c,confirmations=2):
        try:return self._verify(tx_hash,contract,c,confirmations)
        except Invalid:raise
        except (KeyError,TypeError,ValueError,AttributeError):raise Invalid('Malformed receipt response') from None
    def _verify(self,tx_hash,contract,c,confirmations=2):
        if type(confirmations) is not int or not 1<=confirmations<=10000:raise Invalid('Confirmation count')
        self.check_chain();hexbytes(contract,20);hexbytes(tx_hash,32)
        receipt=self.call('eth_getTransactionReceipt',[tx_hash])
        if not receipt or receipt.get('status')!='0x1' or receipt.get('transactionHash','').lower()!=tx_hash.lower():raise Invalid('Missing/failed receipt')
        if receipt.get('to','').lower()!=contract.lower():raise Invalid('Wrong transaction destination')
        number=int(receipt['blockNumber'],16);block=self.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        if not block or block['hash'].lower()!=receipt['blockHash'].lower():raise Invalid('Noncanonical receipt block')
        depth=int(self.call('eth_blockNumber',[]),16)-number+1
        if depth<confirmations:raise Invalid('Insufficient confirmation depth')
        matches=[x for x in receipt['logs'] if x['address'].lower()==contract.lower() and x.get('topics')==[EVENT_TOPIC,'0x'+c['lineage']]]
        expected=word(c['sequence'])+word(c['tick'])+''.join(c[k] for k in ['graph','model','parent','checkpoint'])
        if len(matches)!=1 or matches[0]['data'].lower()!='0x'+expected or matches[0].get('removed',False):raise Invalid('Checkpoint event mismatch')
        # Check again after the other reads to catch a reorg during verification.
        repeated=self.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        if not repeated or repeated['hash']!=block['hash']:raise Invalid('Block changed during verification')
        return {'chain_id':self.expected_chain,'transaction_hash':tx_hash,'block_number':number,'confirmations':depth,'checkpoint_hash':c['checkpoint'],'rpc_trust':'Configured RPC; not a BSC consensus light-client proof'}
