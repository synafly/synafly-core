"""Experimental bounded loopback ingress; existing read/cache semantics unchanged."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from .rpc import RpcError, encode, load_json


class AsyncIngress:
    def __init__(self, routes, *, workers=16, capacity=1024, gate=None):
        if type(workers) is not int or not 1 <= workers <= 16 or type(capacity) is not int or not 1 <= capacity <= 2048:
            raise ValueError('Ingress bounds')
        self.routes = routes; self.capacity = capacity; self.gate = gate
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='local-edge')
        self.slots = asyncio.Semaphore(workers); self.server = None; self.tasks = set()
        self.counts = Counter(); self.active = self.working = self.connections = 0

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, backlog=2048, limit=8192)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self.server is not None: self.server.close(); await self.server.wait_closed()
        if self.gate is not None: self.gate.set()
        if self.tasks:
            done, pending = await asyncio.wait(tuple(self.tasks), timeout=12)
            for task in pending: task.cancel()
            if pending: await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task(); self.tasks.add(task); admitted = False
        self.connections += 1; self.counts['peak_connections'] = max(self.counts['peak_connections'], self.connections)
        async def reply(status, value):
            raw = b'' if value is None else encode(value)
            head = f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n'.encode()
            writer.write(head + raw); await writer.drain()
            self.counts['response_body_bytes'] += len(raw); self.counts['responses_' + str(status)] += 1
        try:
            if self.connections > 2048:
                self.counts['connection_rejections'] += 1; await reply(503, {'error': 'Connection capacity'}); return
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            lines = header.decode('ascii').split('\r\n'); method, path, protocol = lines[0].split(' ')
            headers = {}
            for line in lines[1:]:
                if not line: continue
                key, value = line.split(':', 1); key = key.lower()
                if key in headers: raise ValueError('Duplicate HTTP header')
                headers[key] = value.strip()
            if method != 'POST' or protocol != 'HTTP/1.1' or path not in self.routes:
                await reply(404, {'error': 'Route'}); return
            if headers.get('host') != '127.0.0.1:' + str(self.port) or 'origin' in headers:
                await reply(403, {'error': 'Loopback programmatic client required'}); return
            size = headers.get('content-length', '')
            if not size.isascii() or not size.isdecimal() or len(size) > 5 or not 0 < int(size) <= 16384 or 'transfer-encoding' in headers:
                raise ValueError('Body framing')
            if headers.get('content-type') != 'application/json': raise ValueError('JSON content type')
            body = await asyncio.wait_for(reader.readexactly(int(size)), 5); value = load_json(body, 16384)
            self.counts['received_requests'] += 1; self.counts['request_body_bytes'] += len(body)
            if self.active >= self.capacity:
                self.counts['admission_rejections'] += 1; await reply(503, {'error': 'Admission capacity'}); return
            self.active += 1; admitted = True; self.counts['admitted_requests'] += 1
            self.counts['peak_admitted'] = max(self.counts['peak_admitted'], self.active)
            if self.gate is not None: await self.gate.wait()
            async with self.slots:
                self.working += 1; self.counts['peak_workers'] = max(self.counts['peak_workers'], self.working)
                try: response = await asyncio.get_running_loop().run_in_executor(self.pool, self.routes[path], value)
                finally: self.working -= 1
            await reply(204 if response is None else 200, response)
        except (ValueError, UnicodeError, RpcError, asyncio.LimitOverrunError):
            self.counts['bad_requests'] += 1
            try: await reply(400, {'error': 'Invalid bounded request'})
            except (ConnectionError, OSError): self.counts['disconnected'] += 1
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            self.counts['transport_errors'] += 1
        except Exception:
            self.counts['internal_errors'] += 1
            try: await reply(500, {'error': 'Internal ingress error'})
            except (ConnectionError, OSError): self.counts['disconnected'] += 1
        finally:
            if admitted: self.active -= 1
            self.connections -= 1; self.tasks.discard(task)
            writer.close()
            try: await writer.wait_closed()
            except (ConnectionError, OSError): pass
