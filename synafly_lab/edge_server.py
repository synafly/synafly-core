"""Loopback-only RPC edge: fixed upstream, no browser access, no blockchain writes."""
import argparse
import json
import re
from .edge import ReadEdge
from .node import BoundedServer
from .rpc import BSC_GENESIS, HttpOrigin, NetworkIdentity, RpcError, encode, load_json
from http.server import BaseHTTPRequestHandler

MAX_BODY = 16384

class EdgeHTTPServer(BoundedServer):
    request_queue_size = 32

def execute(edge, request):
    if type(request) is not dict or request.get('jsonrpc') != '2.0' or type(request.get('method')) is not str or set(request) - {'jsonrpc', 'method', 'params', 'id'}:
        return RpcError(-32600, 'Invalid Request').response()
    notification = 'id' not in request
    request_id = request.get('id')
    if request_id is not None and not (type(request_id) is int and -(2**53 - 1) <= request_id <= 2**53 - 1 or type(request_id) is str and len(request_id) <= 64):
        return RpcError(-32600, 'Invalid request ID').response()
    try:
        result = edge.read(request['method'], request.get('params', []))
        response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
    except RpcError as exc:
        response = exc.response(request_id)
    return None if notification else response

def dispatch(edge, value):
    if type(value) is list:
        if not 1 <= len(value) <= 8:
            return RpcError(-32600, 'Batch must contain 1 to 8 requests').response()
        # Sequential, bounded batch execution. No unbounded task spawning.
        results = [execute(edge, item) for item in value]
        return [item for item in results if item is not None] or None
    return execute(edge, value)

def serve(edge, port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def log_message(self, *args):
            pass  # Do not log wallet addresses, request bodies or client identities.
        def reply(self, value, status=200):
            raw = b'' if value is None else encode(value)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
        def allowed(self):
            if self.headers.get('Origin') is not None:
                self.reply({'error': 'Browser origins are not enabled'}, 403)
                return False
            host = self.headers.get('Host', '')
            if host not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                self.reply({'error': 'Loopback Host required'}, 403)
                return False
            return True
        def do_GET(self):
            if not self.allowed(): return
            if self.path == '/health':
                return self.reply({'mode': 'bssr-read-only-research', 'chain_id': edge.identity.chain_id,
                    'genesis_hash': edge.identity.genesis_hash, 'trust': 'Configured upstream, not consensus proof'})
            if self.path == '/metrics': return self.reply({'edge': edge.stats(), 'origin': edge.origin.counters()})
            self.reply({'error': 'Not found'}, 404)
        def do_POST(self):
            if not self.allowed(): return
            if self.path != '/rpc': return self.reply({'error': 'Not found'}, 404)
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                return self.reply({'error': 'JSON with Content-Length required'}, 415)
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,6}', lengths[0]):
                return self.reply({'error': 'One Content-Length required'}, 400)
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY: return self.reply({'error': 'Body limit'}, 413)
            try:
                raw = self.rfile.read(size)
                if len(raw) != size: raise RpcError(-32700, 'Incomplete JSON body')
                result = dispatch(edge, load_json(raw, MAX_BODY))
                self.reply(result, 204 if result is None else 200)
            except RpcError as exc:
                self.reply(exc.response())
            except (TimeoutError, ConnectionError):
                self.close_connection = True
    return EdgeHTTPServer(('127.0.0.1', port), Handler)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--upstream', required=True, help='Explicit bare HTTPS origin; HTTP only for loopback fixtures')
    p.add_argument('--chain-id', type=int, default=56)
    p.add_argument('--genesis-hash', default=BSC_GENESIS)
    p.add_argument('--port', type=int, default=8831)
    p.add_argument('--cache-entries', type=int, default=128)
    p.add_argument('--no-coalescing', action='store_true')
    a = p.parse_args()
    client = HttpOrigin(a.upstream, NetworkIdentity(a.chain_id, a.genesis_hash))
    edge = ReadEdge(client, cache_entries=a.cache_entries, coalesce=not a.no_coalescing)
    server = serve(edge, a.port)
    print(json.dumps({'mode': 'bssr-read-only-research', 'port': server.server_port,
        'chain_id': a.chain_id, 'policy': 'Only noncanonical-required blockHash reads may be cached'}), flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
