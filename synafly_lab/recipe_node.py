"""Owned loopback Anvil fixture; explicit preparation then read-only evaluation."""
from collections import Counter
from contextlib import AbstractContextManager
import http.client
import socket
import subprocess
import time
from .rpc import encode, load_json

READ_METHODS = frozenset({'eth_chainId', 'debug_traceCall', 'eth_call', 'eth_getStorageAt',
                          'eth_getBlockByNumber', 'web3_sha3', 'eth_getCode'})
SETUP_METHODS = frozenset({'anvil_setCode', 'anvil_setStorageAt', 'evm_mine'})


class RecipeNode(AbstractContextManager):
    def __init__(self):
        self.process = None; self.calls = Counter(); self.sealed = False

    def __enter__(self):
        if self.process is not None: raise ValueError('Fixture already started')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.process = subprocess.Popen(['anvil', '--host', '127.0.0.1', '--port', str(self.port),
            '--chain-id', '1337', '--accounts', '0', '--no-mining', '--timestamp', '1735689600'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until = time.monotonic() + 8
            while True:
                if self.process.poll() is not None: raise RuntimeError('Owned local node exited')
                try:
                    if self.call('eth_chainId', []) != '0x539': raise ValueError('Local chain ID mismatch')
                    break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= until: raise RuntimeError('Owned local node readiness timeout')
                    time.sleep(.05)
            self.genesis = self.call('eth_getBlockByNumber', ['0x0', False])
            return self
        except BaseException:
            self.__exit__(None, None, None); raise

    def __exit__(self, *args):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()

    def seal(self):
        """Mine only the owned fixture and reject all later setup/state-mutation calls."""
        if self.sealed: raise ValueError('Fixture already sealed')
        self.call('evm_mine', [1735689601])
        block = self.call('eth_getBlockByNumber', ['latest', False])
        self.sealed = True
        return block

    def call(self, method, params):
        if (method not in READ_METHODS | SETUP_METHODS or self.sealed and method in SETUP_METHODS
                or self.process is None or self.process.poll() is not None):
            raise ValueError('Owned fixture method/lifecycle/phase')
        request = encode({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        if len(request) > 65536: raise ValueError('Request bound')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('POST', '/', request, {'Content-Type': 'application/json'})
            response = connection.getresponse(); raw = response.read(4 * 1024 * 1024 + 1)
            value = load_json(raw, 4 * 1024 * 1024)
            if response.status != 200 or type(value) is not dict or value.get('id') != 1 or 'error' in value:
                raise ValueError('Local recipe RPC failed: ' + method)
            self.calls[method] += 1
            return value['result']
        finally: connection.close()

    def trace(self, request, block='latest'):
        return self.call('debug_traceCall', [request, block, {'enableMemory': True, 'disableStorage': True}])
