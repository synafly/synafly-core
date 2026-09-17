"""Bounded local accounting hash chain; NOT a trustless proof or reward claim."""
from collections import deque
import hashlib
import secrets
import threading
import time
from .rpc import encode

ZERO = '0x' + '00' * 32

def digest(value): return '0x' + hashlib.sha256(encode(value)).hexdigest()


class ReceiptLog:
    def __init__(self, graph_hash, capacity=1024):
        if type(capacity) is not int or not 1 <= capacity <= 4096: raise ValueError('Receipt capacity')
        self.lock = threading.Lock(); self.rows = deque(maxlen=capacity)
        self.lineage = '0x' + secrets.token_hex(32)
        self.graph = graph_hash
        self.model = digest('synafly.offload-accounting.v1')
        self.count = 0; self.parent = ZERO

    def append(self, request, source):
        if not request.block.cacheable or request.method != 'eth_getStorageAt': return
        with self.lock:
            sequence = self.count
            row = {'schema': 'synafly.offload-receipt.v1', 'timestamp': time.time_ns() // 1000000,
                   'block_hash': request.block.value, 'target_contract': request.arguments[0],
                   'target_slot': request.arguments[1], 'upstream_saved': 1, 'source': source,
                   'lineage': self.lineage, 'sequence': sequence, 'parent': self.parent}
            row['receipt_hash'] = digest(row)
            row['commitment'] = {'lineage': self.lineage, 'graph': self.graph, 'model': self.model,
                                 'checkpoint': row['receipt_hash'], 'parent': self.parent,
                                 'sequence': sequence, 'tick': sequence}
            self.rows.append(row); self.parent = row['receipt_hash']; self.count += 1

    def snapshot(self):
        with self.lock:
            return {'schema': 'synafly.offload-receipts.v1', 'total': self.count,
                    'retained': list(self.rows), 'head': self.parent,
                    'trust': 'Operator-reported accounting; no independent proof of avoided origin I/O'}
