"""Bounded asynchronous HTTP admission with a fixed pool of RPC workers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hmac
import socket
import threading
from .edge_server import dispatch
from .rpc import RpcError, encode, load_json
from .peer import origin


class AsyncDaemonServer:
    def __init__(self, daemon, port=8545, *, host='127.0.0.1', peer=False,
                 token='', origins=(), allowed_hosts=(), capacity=1024, workers=16):
        if host not in {'127.0.0.1','0.0.0.0'}: raise ValueError('Bind address')
        if token and (not token.isascii() or not 24<=len(token)<=256 or any(c.isspace() for c in token)): raise ValueError('Access token must be 24..256 non-whitespace ASCII characters')
        if host!='127.0.0.1' and len(token)<24: raise ValueError('Remote binding requires an access token')
        if peer and daemon.mesh is None: raise ValueError('Peer endpoint needs mesh configuration')
        if type(capacity) is not int or not 1<=capacity<=5000 or type(workers) is not int or not 1<=workers<=16:
            raise ValueError('Ingress resource bounds')
        self.daemon,self.peer,self.token=daemon,peer,token
        self.origins=frozenset(origin(o) for o in origins)
        self.capacity,self.workers=capacity,workers
        self.socket=socket.socket();self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:self.socket.bind((host,port));self.socket.listen(2048);self.socket.setblocking(False)
        except BaseException:self.socket.close();raise
        self.server_port=self.socket.getsockname()[1]
        self.hosts={f'127.0.0.1:{self.server_port}',f'localhost:{self.server_port}',*allowed_hosts}
        self.overload=0;self.active=0;self.connections=0;self.peak_admitted=0;self.transport_errors=0
        self.tasks=set();self.ready=threading.Event();self.finished=threading.Event()
        self.loop=None;self.stop=None

    def serve_forever(self,poll_interval=.1):
        try:asyncio.run(self.run())
        finally:self.finished.set()

    async def run(self):
        self.loop=asyncio.get_running_loop();self.stop=asyncio.Event()
        self.pool=ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='synafly-rpc')
        self.slots=asyncio.Semaphore(self.workers)
        server=await asyncio.start_server(self.handle,sock=self.socket,limit=8192)
        self.ready.set()
        try:await self.stop.wait()
        finally:
            server.close();await server.wait_closed()
            if self.tasks:
                _,pending=await asyncio.wait(tuple(self.tasks),timeout=25)
                for task in pending:task.cancel()
                if pending:await asyncio.gather(*pending,return_exceptions=True)
            await asyncio.to_thread(self.pool.shutdown,wait=True,cancel_futures=True)

    def shutdown(self):
        if self.finished.is_set():return
        if not self.ready.wait(5):raise RuntimeError('Ingress did not start')
        self.loop.call_soon_threadsafe(self.stop.set)
        self.finished.wait()

    def server_close(self):self.socket.close()

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task);self.connections+=1;admitted=False
        cors=None
        async def reply(status,value,preflight=False):
            raw=b'' if value is None else encode(value)
            head=f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n'
            if cors:head+=f'Access-Control-Allow-Origin: {cors}\r\nVary: Origin\r\n'
            if preflight:head+='Access-Control-Allow-Methods: POST\r\nAccess-Control-Allow-Headers: Content-Type, Authorization\r\n'
            writer.write(head.encode()+b'\r\n'+raw)
            await asyncio.wait_for(writer.drain(),5)
        try:
            if self.connections>self.capacity+128:
                self.overload+=1;await reply(503,{'error':'Connection capacity'});return
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),5)
            lines=header.decode('ascii').split('\r\n');verb,path,protocol=lines[0].split(' ')
            if protocol!='HTTP/1.1':raise ValueError('HTTP version')
            headers={}
            for line in lines[1:]:
                if not line:continue
                key,value=line.split(':',1);key=key.lower()
                if key in headers or not key or any(c.isspace() for c in key):raise ValueError('Duplicate/invalid header')
                headers[key]=value.strip()
            if headers.get('host') not in self.hosts:
                await reply(403,{'error':'Host not allowed'});return
            if 'origin' in headers:
                if self.peer or headers['origin'] not in self.origins:
                    await reply(403,{'error':'Origin not allowed'});return
                cors=headers['origin']
            if verb=='OPTIONS' and not self.peer and path in {'/','/rpc'}:
                await reply(204,None,True);return
            if self.token and not hmac.compare_digest(headers.get('authorization',''),'Bearer '+self.token):
                await reply(401,{'error':'Authorization required'});return
            if verb=='GET' and not self.peer:
                if path=='/health':await reply(200,{'mode':'read-only-edge','chain_id':self.daemon.identity.chain_id,'mesh':bool(self.daemon.mesh)})
                elif path=='/metrics':await reply(200,{**self.daemon.metrics(),'http_overload_rejections':self.overload,'peak_admitted':self.peak_admitted,'admitted_active':self.active,'transport_errors':self.transport_errors})
                elif path=='/receipts':await reply(200,self.daemon.receipts.snapshot())
                else:await reply(404,{'error':'Route'})
                return
            if verb!='POST' or path not in ({'/peer/read'} if self.peer else {'/','/rpc'}):
                await reply(404,{'error':'Route'});return
            size=headers.get('content-length','')
            if not size.isascii() or not size.isdecimal() or len(size)>6 or 'transfer-encoding' in headers:
                await reply(400,{'error':'Invalid framing'});return
            if not 0<int(size)<=16384:await reply(413,{'error':'Body limit'});return
            if headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
                await reply(415,{'error':'JSON required'});return
            body=await asyncio.wait_for(reader.readexactly(int(size)),5)
            try:value=load_json(body,16384)
            except RpcError as exc:await reply(200,exc.response());return
            if self.active>=self.capacity:
                self.overload+=1;await reply(503,{'error':'Admission capacity'});return
            self.active+=1;admitted=True;self.peak_admitted=max(self.peak_admitted,self.active)
            async with self.slots:
                def execute():
                    try:
                        return self.daemon.mesh.receive(value,headers.get('authorization','')) if self.peer else dispatch(self.daemon,value)
                    except RpcError as exc:return exc.response()
                result=await self.loop.run_in_executor(self.pool,execute)
            await reply(204 if result is None else 200,result)
        except (ValueError,UnicodeError,asyncio.LimitOverrunError):
            try:await reply(400,{'error':'Malformed bounded HTTP'})
            except (OSError,TimeoutError):self.transport_errors+=1
        except (TimeoutError,asyncio.IncompleteReadError,OSError):self.transport_errors+=1
        except Exception:
            self.transport_errors+=1
            try:await reply(500,RpcError(-32603,'Internal error').response())
            except (OSError,TimeoutError):pass
        finally:
            if admitted:self.active-=1
            self.connections-=1;self.tasks.discard(task);writer.close()
            try:await writer.wait_closed()
            except OSError:pass
