"""Writes are confined to a newly spawned owned loopback Anvil. Never accepts a URL."""
from collections import Counter
import socket
import subprocess
import time
from .quorum_rpc import ReadRPC, READS, RegistryRPCError
from .rpc import quantity

LOCAL_WRITES=frozenset({'eth_sendTransaction','eth_sign','anvil_setCode','anvil_setStorageAt','evm_mine'})


class OwnedAnvil(ReadRPC):
    def __init__(self):
        self.process=None;self.counts=Counter()
    def __enter__(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.process=subprocess.Popen(['anvil','--host','127.0.0.1','--port',str(port),'--chain-id','97','--accounts','4','--timestamp','1735689600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        super().__init__('http://127.0.0.1:'+str(port))
        try:
            deadline=time.monotonic()+10
            while True:
                if self.process.poll() is not None:raise ValueError('Owned Anvil exited')
                try:
                    if self.call('eth_chainId',[])!='0x61':raise ValueError('Owned chain identity')
                    break
                except OSError:
                    if time.monotonic()>deadline:raise ValueError('Owned Anvil readiness')
                    time.sleep(.05)
            self.accounts=self.call('eth_accounts',[])
            self.genesis=self.call('eth_getBlockByNumber',['0x0',False])['hash']
            return self
        except BaseException:self.__exit__(None,None,None);raise
    def call(self,method,params):
        if self.process is None or self.process.poll() is not None or method not in READS|LOCAL_WRITES|{'eth_accounts'}:
            raise ValueError('Owned node lifecycle/method boundary')
        self.counts[method]+=1
        return self._exchange(method,params)
    def receipt(self,tx_hash):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            value=self.call('eth_getTransactionReceipt',[tx_hash])
            if value is not None:return value
            time.sleep(.02)
        # Caller must reconcile this hash; never blindly resubmit.
        raise TimeoutError('Transaction receipt timeout; reconcile the existing transaction hash')
    def __exit__(self,*_):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
