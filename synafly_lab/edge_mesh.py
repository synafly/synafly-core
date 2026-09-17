"""Explicit trusted-peer, one-hop cache mesh using PR #5's fixed role adjacency."""
from collections import Counter
import hashlib
import hmac
import http.client
from pathlib import Path
import threading
from urllib.parse import urlsplit
from .peer import origin
from .rpc import RpcError, cache_key, encode, load_json, parse_read, validate_result

TOPOLOGY_SHA256 = '35408246f15236040b3937f5da69bc6609ee219dc1e818f02c39ca8c0ac8b056'


def load_topology(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TOPOLOGY_SHA256: raise ValueError('Topology integrity')
    value = load_json(raw)
    return tuple(tuple(b for a, b in value['edges'] if a == i) for i in range(58))


class MeshOrigin:
    def __init__(self, upstream, role, peers, token, topology, *, timeout=0.3):
        if type(role) is not int or not 0 <= role < 58: raise ValueError('Role must be 0..57')
        if len(token) < 24 or len(token) > 256 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('Set SYNAFLY_MESH_TOKEN to 24..256 non-whitespace ASCII characters')
        if type(timeout) not in (int, float) or not 0.05 <= timeout <= 1: raise ValueError('Peer timeout bound')
        if len(peers) > 6 or any(type(k) is not int or k not in topology[role] for k in peers):
            raise ValueError('Peers must be configured outgoing neighbors of this PR #5 role (max six)')
        self.upstream, self.role = upstream, role
        self.peers = {k: origin(v) for k, v in peers.items()}
        self.token, self.topology, self.peer_timeout = token, topology, timeout
        self.identity = upstream.identity
        self.timeout = upstream.timeout + len(peers) * timeout
        self.local = threading.local(); self.lock = threading.Lock(); self.counts = Counter()
        self.edge = None

    def lane(self, name): return self.upstream.lane(name)
    def counters(self): return self.upstream.counters()
    def stats(self):
        with self.lock: return dict(self.counts)
    def count(self, key, value=1):
        with self.lock: self.counts[key] += value

    def query(self, role, method, params, key):
        url = urlsplit(self.peers[role])
        cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        connection = cls(url.hostname, url.port, timeout=self.peer_timeout)
        body = encode({'schema': 'synafly.mesh-read.v1', 'chain_id': self.identity.chain_id,
                       'genesis_hash': self.identity.genesis_hash, 'sender_role': self.role,
                       'key': key, 'method': method, 'params': params})
        timer = None
        try:
            connection.connect()
            sock = connection.sock
            def abort():
                try: sock.shutdown(2)
                except OSError: pass
            timer = threading.Timer(self.peer_timeout, abort); timer.daemon = True; timer.start()
            self.count('queries'); self.count('request_json_bytes', len(body))
            connection.request('POST', '/peer/read', body, {'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + self.token, 'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError('Peer HTTP status')
            raw = response.read(262145); self.count('response_json_bytes', len(raw))
            value = load_json(raw)
            if type(value) is not dict or set(value) != {'schema','role','key','hit','value'} or value['schema'] != 'synafly.mesh-result.v1' or type(value['role']) is not int or value['role'] != role or value['key'] != key or type(value['hit']) is not bool:
                raise ValueError('Peer response envelope')
            if not value['hit']:
                if value['value'] is not None: raise ValueError('Peer miss value')
                return None
            return validate_result(method, value['value'])
        finally:
            if timer: timer.cancel()
            connection.close()

    def call(self, method, params):
        self.local.peer_hit = False
        request = parse_read(method, params)
        if request.block.cacheable:
            key = cache_key(self.identity, request)
            # Deterministic key-based ordering of only the configured synaptic neighbors.
            for role in sorted(self.peers, key=lambda r: hashlib.sha256(f'{key}:{r}'.encode()).digest()):
                try:
                    value = self.query(role, method, params, key)
                    if value is not None:
                        self.local.peer_hit = True; self.count('hits'); return value
                except (ValueError, OSError, http.client.HTTPException): self.count('failures')
            self.count('origin_fallbacks')
        return self.upstream.call(method, params)

    def receive(self, value, authorization):
        if not hmac.compare_digest(authorization, 'Bearer ' + self.token): raise RpcError(-32001, 'Peer authentication required')
        fields = {'schema','chain_id','genesis_hash','sender_role','key','method','params'}
        if type(value) is not dict or set(value) != fields or value['schema'] != 'synafly.mesh-read.v1':
            raise RpcError(-32600, 'Peer envelope')
        sender = value['sender_role']
        if type(sender) is not int or not 0 <= sender < 58 or self.role not in self.topology[sender]:
            raise RpcError(-32602, 'Non-neighbor sender')
        if type(value['chain_id']) is not int or value['chain_id'] != self.identity.chain_id or value['genesis_hash'] != self.identity.genesis_hash:
            raise RpcError(-32602, 'Peer network mismatch')
        if value['method'] not in {'eth_getCode','eth_getStorageAt'}: raise RpcError(-32601, 'Peer method')
        request = parse_read(value['method'], value['params'])
        key = cache_key(self.identity, request)
        if not request.block.cacheable or value['key'] != key: raise RpcError(-32602, 'Peer key/selector mismatch')
        result = self.edge.peek(request.method, request.wire_params())
        self.count('received'); self.count('served_hits', int(result is not None))
        return {'schema':'synafly.mesh-result.v1','role':self.role,'key':key,'hit':result is not None,'value':result}
