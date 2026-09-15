"""Controllable synthetic origin for local tests; never a public-chain dataset."""
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import threading
import time
from synafly_lab.rpc import NetworkIdentity, READ_METHODS, encode

GENESIS = '0x' + '01' * 32
# A and B model competing blocks at one synthetic height, not ancestor/descendant.
BLOCK_A = '0x' + '11' * 32
BLOCK_B = '0x' + '22' * 32
BLOCK_MISSING = '0x' + '33' * 32
ADDRESS = '0x' + 'ab' * 20
IDENTITY = NetworkIdentity(1337, GENESIS)

def state_value(method, params, block_hash):
    seed = encode([method, params[:-1], block_hash])
    number = int.from_bytes(hashlib.sha256(seed).digest()[:4], 'big') + 1
    if method in {'eth_getBalance', 'eth_getTransactionCount'}: return hex(number)
    if method == 'eth_getStorageAt': return '0x' + format(number, '064x')
    return '0x6000' + format(number, '08x')

class OriginFixture(AbstractContextManager):
    def __init__(self):
        self.condition = threading.Condition()
        self.reads, self.metadata, self.active, self.peak = 0, 0, 0, 0
        self.head, self.fault, self.gate = BLOCK_A, None, None
        self.last_params = None
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method, params = body['method'], body['params']
                with owner.condition:
                    owner.requests.append(body)
                    if method in READ_METHODS:
                        owner.reads += 1
                        owner.active += 1
                        owner.peak = max(owner.peak, owner.active)
                        owner.last_params = params
                    else: owner.metadata += 1
                    owner.condition.notify_all()
                if method in READ_METHODS:
                    try:
                        if owner.gate is not None and not owner.gate.wait(5):
                            raise RuntimeError('Fixture gate was not released')
                        selector = params[-1]
                        block = selector.get('blockHash') if isinstance(selector, dict) else owner.head
                        if block not in {BLOCK_A, BLOCK_B}:
                            payload = {'error': {'code': -32001, 'message': 'Unknown block'}}
                        elif isinstance(selector, dict) and selector.get('requireCanonical') and block != owner.head:
                            payload = {'error': {'code': -32000, 'message': 'Noncanonical block'}}
                        else: payload = {'result': state_value(method, params, block)}
                    finally:
                        with owner.condition: owner.active -= 1
                elif method == 'eth_chainId': payload = {'result': '0x1' if owner.fault == 'chain' else '0x539'}
                elif method == 'eth_getBlockByNumber':
                    payload = {'result': {'number': '0x0', 'hash': BLOCK_B if owner.fault == 'genesis' else GENESIS}}
                else: payload = {'error': {'code': -32601, 'message': 'No method'}}
                result = {'jsonrpc': '2.0', 'id': body['id'], **payload}
                if method in READ_METHODS:
                    if owner.fault == 'id': result['id'] = body['id'] + 1
                    if owner.fault == 'bool-id': result['id'] = True
                    if owner.fault == 'both': result['error'] = {'code': -32000, 'message': 'bad'}
                    if owner.fault == 'result': result['result'] = 'not-hex'
                    if owner.fault == 'null': result['result'] = None
                    if owner.fault == 'error': result = {'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': -32000, 'message': 'failed'}}
                    if owner.fault == 'error-code': result = {'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': True, 'message': 'bad'}}
                raw = encode(result)
                if method in READ_METHODS and owner.fault == 'duplicate': raw = raw[:-1] + b',"result":"0x1"}'
                if method in READ_METHODS and owner.fault == 'oversize': raw = b' ' * 262145
                if method in READ_METHODS and owner.fault == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', owner.url + '/not-allowed')
                    self.end_headers()
                    return
                if method in READ_METHODS and owner.fault == 'slow-header':
                    try:
                        self.wfile.write(b'HTTP/1.0 200 OK\r\nX-Slow: ')
                        for _ in range(200):
                            self.wfile.write(b'x');self.wfile.flush();time.sleep(.03)
                    except (BrokenPipeError, ConnectionResetError): pass
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html' if owner.fault == 'content-type' and method in READ_METHODS else 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    if method in READ_METHODS and owner.fault == 'slow-body':
                        for byte in raw:
                            self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.03)
                    else:self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.release()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
    def pause(self): self.gate = threading.Event()
    def release(self):
        if self.gate is not None: self.gate.set()
    def wait_reads(self, count, timeout=3):
        with self.condition:
            if not self.condition.wait_for(lambda: self.reads >= count, timeout):
                raise AssertionError('Expected origin reads did not arrive')
