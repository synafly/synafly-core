"""Opt-in latest-block sampler; not a mempool listener or a catch-up indexer."""
from collections import Counter
import threading
from .edge_daemon import parse_call
from .rpc import RpcError, block_ref, hex_data


class BlockWarmer:
    def __init__(self, daemon, interval=12):
        if type(interval) not in (int,float) or not 5 <= interval <= 300: raise ValueError('Poll interval 5..300 seconds')
        self.daemon=daemon;self.interval=interval;self.stop=threading.Event()
        self.lock=threading.Lock();self.counts=Counter();self.last=None
        self.thread=threading.Thread(target=self.run,name='synafly-block-watcher')
    def stats(self):
        with self.lock: return dict(self.counts)
    def poll(self):
        with self.daemon.origin.lane('watcher'):
            header=self.daemon.origin.call('eth_getBlockByNumber',['latest',False])
            if not header or header.get('hash') is None or header.get('number') is None: return
            block_hash=hex_data(header['hash'],32)
            if block_hash==self.last: return
            block=self.daemon.origin.call('eth_getBlockByNumber',[header['number'],True])
            if not block or block.get('hash')!=block_hash:
                with self.lock:self.counts['reorg_races']+=1
                return
            self.last=block_hash
            transactions=block.get('transactions')
            if type(transactions) is not list: raise ValueError('Transactions array')
            with self.lock:
                self.counts['blocks']+=1;self.counts['transactions_omitted']=self.counts['transactions_omitted']+max(0,len(transactions)-64)
            for tx in transactions[:64]:
                if type(tx) is not dict or not tx.get('to'):continue
                try:
                    call={k:tx[k] for k in ('to','from','input','value') if k in tx}
                    call,reference=parse_call([call,{'blockHash':block_hash,'requireCanonical':False}])
                    selector=call.get('input','0x')[2:10]
                    if not any(namespace[1]==selector for namespace in self.daemon.prefetch.index.namespaces):continue
                    self.daemon.prefetch.schedule(call,reference)
                except (RpcError,ValueError):
                    with self.lock:self.counts['skipped_transactions']+=1
    def run(self):
        while not self.stop.is_set():
            try:self.poll()
            except (RpcError,ValueError,KeyError,TypeError):
                with self.lock:self.counts['errors']+=1
            if self.stop.wait(self.interval):break
    def start(self):self.thread.start()
    def close(self):self.stop.set();self.thread.join()
