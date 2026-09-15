"""Bounded JSON-RPC reads; no keys, transactions or consensus-verification claims."""
from dataclasses import dataclass
import hashlib
import http.client
import itertools
import json
import math
import re
import socket
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from .peer import origin

BSC_GENESIS = '0x0d21840abff46b96c84b2ac9e10e4f5cdaeb5693cb665db62a2f3b02d2d57b5b'
READ_METHODS = frozenset({'eth_getBalance', 'eth_getTransactionCount', 'eth_getCode', 'eth_getStorageAt'})
METADATA_METHODS = frozenset({'eth_chainId', 'eth_getBlockByNumber'})
MAX_RESPONSE = 262144

class RpcError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message
    def response(self, request_id=None):
        return {'jsonrpc': '2.0', 'id': request_id,
                'error': {'code': self.code, 'message': self.message}}

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode()

def load_json(raw, limit=MAX_RESPONSE):
    if len(raw) > limit:
        raise RpcError(-32700, 'JSON size limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RpcError(-32700, 'Duplicate JSON member')
            result[key] = value
        return result
    def check(value, depth=0):
        if depth > 12:
            raise RpcError(-32700, 'JSON depth limit')
        if isinstance(value, dict):
            if len(value) > 128:
                raise RpcError(-32700, 'JSON object limit')
            for item in value.values(): check(item, depth + 1)
        elif isinstance(value, list):
            if len(value) > 4096:
                raise RpcError(-32700, 'JSON array limit')
            for item in value: check(item, depth + 1)
        elif type(value) is int and value.bit_length() > 256:
            raise RpcError(-32700, 'JSON integer limit')
        elif type(value) is float and not math.isfinite(value):
            raise RpcError(-32700, 'Nonfinite JSON number')
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        check(value)
        return value
    except RpcError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RpcError(-32700, 'Malformed JSON') from exc

def hex_data(value, size=None, maximum=MAX_RESPONSE // 2):
    if type(value) is not str or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', value):
        raise RpcError(-32602, 'Expected hexadecimal data')
    length = (len(value) - 2) // 2
    if length > maximum or size is not None and length != size:
        raise RpcError(-32602, 'Hexadecimal data length')
    return value.lower()

def quantity(value, bits=256):
    if type(value) is not str or not re.fullmatch(r'0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)', value):
        raise RpcError(-32602, 'Expected canonical hexadecimal quantity')
    if len(value) - 2 > (bits + 3) // 4 or int(value, 16).bit_length() > bits:
        raise RpcError(-32602, 'Quantity bound')
    return value.lower()

@dataclass(frozen=True)
class BlockRef:
    kind: str
    value: str
    require_canonical: bool = False
    @property
    def cacheable(self):
        return self.kind == 'hash' and not self.require_canonical
    def wire(self):
        if self.kind == 'hash':
            return {'blockHash': self.value, 'requireCanonical': self.require_canonical}
        return self.value

def block_ref(value):
    if type(value) is str:
        if value in {'latest', 'pending', 'safe', 'finalized', 'earliest'}:
            return BlockRef('tag', value)
        return BlockRef('number', quantity(value, 64))
    if type(value) is dict:
        if set(value) == {'blockNumber'}:
            return BlockRef('number', quantity(value['blockNumber'], 64))
        if set(value) in ({'blockHash'}, {'blockHash', 'requireCanonical'}):
            flag = value.get('requireCanonical', False)
            if type(flag) is not bool:
                raise RpcError(-32602, 'Canonicality must be a boolean')
            return BlockRef('hash', hex_data(value['blockHash'], 32), flag)
    raise RpcError(-32602, 'Unsupported block selector')

@dataclass(frozen=True)
class ReadRequest:
    method: str
    arguments: tuple
    block: BlockRef
    def wire_params(self):
        return [*self.arguments, self.block.wire()]

def parse_read(method, params):
    if type(method) is not str or method not in READ_METHODS:
        raise RpcError(-32601, 'Read method not supported')
    count = 2 if method == 'eth_getStorageAt' else 1
    if type(params) is not list or len(params) not in (count, count + 1):
        raise RpcError(-32602, 'Read parameter count')
    arguments = [hex_data(params[0], 20)]
    if count == 2: arguments.append(quantity(params[1]))
    block = block_ref(params[count] if len(params) > count else 'latest')
    return ReadRequest(method, tuple(arguments), block)

@dataclass(frozen=True)
class NetworkIdentity:
    chain_id: int
    genesis_hash: str
    def __post_init__(self):
        if type(self.chain_id) is not int or not 0 < self.chain_id < 2**63:
            raise RpcError(-32602, 'Chain identity bound')
        object.__setattr__(self, 'genesis_hash', hex_data(self.genesis_hash, 32))

def cache_key(identity, request):
    # Request IDs and upstream URLs are deliberately not state identity.
    return hashlib.sha256(encode(['synafly.rpc-cache.v1', identity.chain_id,
        identity.genesis_hash, request.method, list(request.arguments),
        request.block.kind, request.block.value, request.block.require_canonical])).hexdigest()

def validate_result(method, value):
    try:
        if method in {'eth_getBalance', 'eth_getTransactionCount'}:
            return quantity(value)
        return hex_data(value, 32 if method == 'eth_getStorageAt' else None)
    except RpcError as exc:
        raise RpcError(-32002, 'Malformed upstream read result') from exc

class HttpOrigin:
    """One operator-selected upstream. RPC counters include failures and bootstrap."""
    def __init__(self, url, identity, timeout=3.0):
        self.url, self.identity = origin(url), identity
        if not isinstance(identity, NetworkIdentity) or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.1 <= timeout <= 10:
            raise RpcError(-32602, 'Origin configuration')
        self.timeout = timeout
        parsed = urlsplit(self.url)
        try: parsed.port
        except ValueError as exc: raise RpcError(-32602, 'Invalid upstream port') from exc
        if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} and any(k in urllib.request.getproxies() for k in ('http', 'https', 'all')):
            raise RpcError(-32602, 'Configured network proxies are not supported by this direct-socket transport')
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._counts = {'rpc_calls': 0, 'metadata_calls': 0, 'state_reads': 0,
                        'request_json_bytes': 0, 'response_json_bytes': 0}
        chain = self.call('eth_chainId', [])
        genesis = self.call('eth_getBlockByNumber', ['0x0', False])
        if quantity(chain, 63) != hex(identity.chain_id) or type(genesis) is not dict:
            raise RpcError(-32003, 'Upstream network identity mismatch')
        try:
            valid = hex_data(genesis.get('hash'), 32) == identity.genesis_hash and quantity(genesis.get('number'), 64) == '0x0'
        except RpcError:
            valid = False
        if not valid: raise RpcError(-32003, 'Upstream genesis mismatch')
    def counters(self):
        with self._lock: return dict(self._counts)
    def _exchange(self, raw):
        parsed = urlsplit(self.url)
        connection_type = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=self.timeout)
        timer = None
        expired = threading.Event()
        start = time.monotonic()
        try:
            # Platform DNS resolution is outside Python's socket-timeout guarantee.
            # After connect, a shutdown timer also bounds a trickling header/body.
            connection.connect()
            sock = connection.sock
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0: raise RpcError(-32002, 'Upstream deadline exceeded')
            def abort():
                expired.set()
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass  # Completion and deadline may race on a closed socket.
            timer = threading.Timer(remaining, abort)
            timer.daemon = True
            timer.start()
            connection.request('POST', '/', body=raw, headers={
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'Connection': 'close', 'User-Agent': 'SynaFly-Research-ReadEdge/0.2'})
            response = connection.getresponse()
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise RpcError(-32002, 'Unexpected upstream HTTP response')
            body = response.read(MAX_RESPONSE + 1)
            with self._lock: self._counts['response_json_bytes'] += len(body)
            if expired.is_set(): raise RpcError(-32002, 'Upstream deadline exceeded')
            return body
        except (OSError, http.client.HTTPException) as exc:
            message = 'Upstream deadline exceeded' if expired.is_set() else 'Upstream unavailable'
            raise RpcError(-32002, message) from exc
        finally:
            if timer is not None: timer.cancel()
            connection.close()
    def call(self, method, params):
        if method not in READ_METHODS | METADATA_METHODS:
            raise RpcError(-32601, 'Upstream method not allowed')
        with self._lock:
            request_id = next(self._ids)
            raw = encode({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            self._counts['rpc_calls'] += 1
            self._counts['metadata_calls' if method in METADATA_METHODS else 'state_reads'] += 1
            self._counts['request_json_bytes'] += len(raw)
        body = self._exchange(raw)
        try:
            value = load_json(body)
        except RpcError as exc:
            raise RpcError(-32002, 'Malformed upstream JSON') from exc
        if type(value) is not dict or value.get('jsonrpc') != '2.0' or type(value.get('id')) is not int or value['id'] != request_id:
            raise RpcError(-32002, 'Upstream response identity mismatch')
        if ('result' in value) == ('error' in value):
            raise RpcError(-32002, 'Upstream result/error ambiguity')
        if 'error' in value:
            error = value['error']
            if type(error) is not dict or type(error.get('code')) is not int or not -(2**31) <= error['code'] < 2**31 or type(error.get('message')) is not str:
                raise RpcError(-32002, 'Malformed upstream error')
            raise RpcError(error['code'], 'Upstream rejected the read')
        return validate_result(method, value['result']) if method in READ_METHODS else value['result']
