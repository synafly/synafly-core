"""Bounded read-only registry RPC and exact preflight/transaction evidence checks."""
import http.client
import itertools
import socket
import threading
from urllib.parse import urlsplit
from .anchor import EVENT_TOPIC, message_calldata, word
from .keccak import keccak256
from .quorum_relayer import abi_commitment, aggregate_signatures, strict_hex, validate_bundle
from .rpc import encode, hex_data, load_json, quantity
from .peer import origin

READS = frozenset({'eth_chainId','eth_blockNumber','eth_getBlockByNumber','eth_getCode',
                   'eth_getStorageAt','eth_call','eth_getTransactionReceipt','eth_getTransactionByHash'})


class RegistryRPCError(ValueError):
    def __init__(self, code, data=None):
        super().__init__('Registry RPC rejected request'); self.code, self.data = code, data


class ReadRPC:
    def __init__(self, url):
        self.url = origin(url); self.ids = itertools.count(1); self.wire = []; self.record = False
    def call(self, method, params):
        if method not in READS: raise ValueError('Read-only registry client')
        return self._exchange(method, params)
    def _exchange(self, method, params):
        endpoint=urlsplit(self.url); request_id=next(self.ids)
        request={'jsonrpc':'2.0','id':request_id,'method':method,'params':params}
        cls=http.client.HTTPSConnection if endpoint.scheme=='https' else http.client.HTTPConnection
        con=cls(endpoint.hostname,endpoint.port,timeout=5); timer=None
        try:
            con.connect(); sock=con.sock
            def abort():
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            timer=threading.Timer(5,abort);timer.daemon=True;timer.start()
            con.request('POST','/',encode(request),{'Content-Type':'application/json','Connection':'close'})
            response=con.getresponse();raw=response.read(2097153)
            if response.status!=200 or response.headers.get_content_type()!='application/json':raise ValueError('Registry HTTP response')
            value=load_json(raw,2097152)
            if type(value) is not dict or value.get('jsonrpc')!='2.0' or type(value.get('id')) is not int or value['id']!=request_id or ('result' in value)==('error' in value):raise ValueError('Registry RPC envelope')
            if self.record:
                if len(self.wire)>=512:raise ValueError('Wire capture bound')
                self.wire.append({'method':method,'params':params,'response':value})
            if 'error' in value:
                error=value['error']
                if type(error) is not dict or type(error.get('code')) is not int:raise ValueError('Registry RPC error')
                raise RegistryRPCError(error['code'],error.get('data'))
            return value['result']
        finally:
            if timer:timer.cancel()
            con.close()


def selector(signature): return keccak256(signature.encode())[:4].hex()


def view(rpc, registry, signature, arguments='', block='latest'):
    return rpc.call('eth_call',[{'to':registry,'data':'0x'+selector(signature)+arguments},block])


def head(rpc, registry, lineage, block='latest'):
    raw=hex_data(view(rpc,registry,'heads(bytes32)',strict_hex(lineage,32)[2:],block),192)[2:]
    words=[raw[i:i+64] for i in range(0,len(raw),64)]
    sequence,tick,exists=(int(x,16) for x in words[3:])
    if sequence>=2**64 or tick>=2**64 or exists not in (0,1):raise ValueError('Registry head ABI')
    return {'graph':'0x'+words[0],'model':'0x'+words[1],'checkpoint':'0x'+words[2],
            'sequence':sequence,'tick':tick,'exists':bool(exists)}


def preflight(rpc,bundle,previous=None):
    validate_bundle(bundle,previous)
    if int(quantity(rpc.call('eth_chainId',[])),16)!=bundle['chain_id']:raise ValueError('Wrong chain')
    header=rpc.call('eth_getBlockByNumber',['latest',False])
    pin={'blockHash':strict_hex(header['hash'],32),'requireCanonical':True}
    code=bytes.fromhex(hex_data(rpc.call('eth_getCode',[bundle['registry'],pin]))[2:])
    if not code or '0x'+keccak256(code).hex()!=bundle['runtime_code_hash']:raise ValueError('Registry runtime binding')
    threshold=int(hex_data(view(rpc,bundle['registry'],'threshold()',block=pin),32),16)
    if threshold!=bundle['threshold']:raise ValueError('Registry threshold mismatch')
    for i,address in enumerate(bundle['committee']):
        if int(hex_data(view(rpc,bundle['registry'],'isWitness(address)',address[2:].rjust(64,'0'),pin),32),16)!=1:
            raise ValueError('Registry witness membership')
        actual=hex_data(view(rpc,bundle['registry'],'witnesses(uint256)',word(i),pin),32)
        if actual!='0x'+address[2:].rjust(64,'0'):raise ValueError('Registry witness order')
    try:view(rpc,bundle['registry'],'witnesses(uint256)',word(len(bundle['committee'])),pin)
    except RegistryRPCError as exc:
        # solc 0.8.30's generated array getter uses an empty revert, not Panic(0x32).
        if exc.code!=3 or exc.data!='0x':raise ValueError('Registry committee length not verified')
    else:raise ValueError('Registry contains additional witnesses')
    actual_message=rpc.call('eth_call',[{'to':bundle['registry'],'data':message_calldata(abi_commitment(bundle['commitment']))},pin])
    if actual_message!=bundle['message_hash']:raise ValueError('Registry message hash mismatch')
    signing=view(rpc,bundle['registry'],'signingDigest((bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64))',message_calldata(abi_commitment(bundle['commitment']))[10:],pin)
    if signing!=bundle['signing_digest']:raise ValueError('Registry signing digest mismatch')
    actual=head(rpc,bundle['registry'],bundle['commitment']['lineage'],pin)
    if previous is None:
        if actual['exists']:raise ValueError('Lineage already committed')
    else:
        if not actual['exists'] or any(actual[k]!=previous['commitment'][k] for k in actual if k!='exists'):
            raise ValueError('Previous checkpoint is not registry head')
    return {'block_hash':header['hash'],'runtime_code_hash':bundle['runtime_code_hash'],
            'threshold':threshold,'configured_witnesses_verified':len(bundle['committee']),'expected_head_matched':True}


def verify_transaction(rpc,bundle,certificate,tx_hash,*,confirmations=1):
    strict_hex(tx_hash,32)
    if type(confirmations) is not int or not 1<=confirmations<=100:raise ValueError('Confirmation bound')
    if int(quantity(rpc.call('eth_chainId',[])),16)!=bundle['chain_id']:raise ValueError('Receipt chain mismatch')
    receipt=rpc.call('eth_getTransactionReceipt',[tx_hash]);transaction=rpc.call('eth_getTransactionByHash',[tx_hash])
    if type(receipt) is not dict or receipt.get('status')!='0x1' or receipt.get('transactionHash')!=tx_hash or receipt.get('to')!=bundle['registry']:raise ValueError('Missing/failed/wrong receipt')
    if type(transaction) is not dict or transaction.get('hash')!=tx_hash or transaction.get('to')!=bundle['registry'] or transaction.get('input')!=certificate['transaction']['data'] or transaction.get('from')!=receipt.get('from') or quantity(transaction.get('value'))!='0x0':raise ValueError('Transaction identity/calldata/value mismatch')
    if 'chainId' in transaction and int(quantity(transaction['chainId']),16)!=bundle['chain_id']:raise ValueError('Transaction chain mismatch')
    if transaction.get('blockHash')!=receipt.get('blockHash') or transaction.get('blockNumber')!=receipt.get('blockNumber'):raise ValueError('Transaction block mismatch')
    gas=int(quantity(receipt['gasUsed']),16)
    if not 0<gas<=int(quantity(transaction['gas']),16):raise ValueError('Gas accounting')
    c=bundle['commitment']; expected='0x'+word(c['sequence'])+word(c['tick'])+''.join(c[k][2:] for k in ('graph','model','parent','checkpoint'))
    logs=receipt.get('logs')
    if type(logs) is not list or len(logs)>32:raise ValueError('Log bound')
    matches=[log for log in logs if log.get('address')==bundle['registry'] and log.get('topics')==[EVENT_TOPIC,c['lineage']]]
    if len(matches)!=1 or matches[0].get('data')!=expected or matches[0].get('removed',False) is not False or matches[0].get('transactionHash')!=tx_hash or matches[0].get('blockHash')!=receipt.get('blockHash'):raise ValueError('CheckpointCommitted event mismatch')
    block=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
    if not block or block['hash']!=receipt['blockHash'] or block['number']!=receipt['blockNumber']:raise ValueError('Receipt block not canonical')
    depth=int(quantity(rpc.call('eth_blockNumber',[])),16)-int(quantity(receipt['blockNumber']),16)+1
    if depth<confirmations:raise ValueError('Insufficient confirmations')
    observed=head(rpc,bundle['registry'],c['lineage'],{'blockHash':receipt['blockHash'],'requireCanonical':True})
    if not observed['exists'] or any(observed[k]!=c[k] for k in observed if k!='exists'):raise ValueError('Committed head mismatch')
    repeated=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
    if not repeated or repeated['hash']!=block['hash']:raise ValueError('Reorg during verification')
    return {'receipt':receipt,'transaction':transaction,'gas_used':gas,'effective_gas_price':receipt.get('effectiveGasPrice'),
            'event_topic':EVENT_TOPIC,'confirmations':depth,'registry_head':observed,
            'scope':'Configured RPC view, not independent consensus/finality proof'}
