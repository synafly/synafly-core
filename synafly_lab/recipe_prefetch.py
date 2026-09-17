"""Bounded, opt-in advisory prefetch. Never supplies eth_call return values."""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading
import time
from .access_recipes import call_context
from .runtime_recipes import materialize, evaluate, RuntimeRecipeIndex
from .keccak import keccak_int, keccak256
from .rpc import RpcError, encode, hex_data, load_json, quantity


def load_catalog(path, identity):
    with Path(path).open('rb') as stream:
        value = load_json(stream.read(1048577), 1048576)
    if type(value) is not dict or set(value) != {'schema', 'chain_id', 'genesis_hash', 'entries'}:
        raise ValueError('Catalog fields')
    if value['schema'] != 'synafly.daemon-recipes.v1' or type(value['chain_id']) is not int or value['chain_id'] != identity.chain_id or value['genesis_hash'] != identity.genesis_hash:
        raise ValueError('Catalog network/schema mismatch')
    rows = value['entries']
    if type(rows) is not list or not 1 <= len(rows) <= 256:
        raise ValueError('Catalog row bound')
    index = RuntimeRecipeIndex()
    for row in rows:
        if type(row) is not dict or set(row) != {'code_hash', 'selector', 'data', 'caller', 'value', 'recipe'}:
            raise ValueError('Catalog entry fields')
        code_hash = hex_data(row['code_hash'], 32)
        selector = hex_data(row['selector'], 4)[2:]
        data = bytes.fromhex(hex_data(row['data'], maximum=256)[2:])
        if data[:4].hex() != selector: raise ValueError('Training selector mismatch')
        context = call_context(data, int(hex_data(row['caller'], 20), 16), int(quantity(row['value']), 16))
        # Validate the whole bounded AST, including guards not taken by this sample.
        recipe = row['recipe']
        if type(recipe) is not dict or set(recipe) != {'accesses', 'guards'}:
            raise ValueError('Catalog recipe fields')
        accesses, guards = recipe['accesses'], recipe['guards']
        if type(accesses) is not list or type(guards) is not list or not 1 <= len(accesses) <= 16 or len(guards) > 16:
            raise ValueError('Catalog recipe bounds')
        for access in accesses:
            if type(access) is not list or len(access) != 2 or access[0] != 'sload':
                raise ValueError('Only SLOAD hints supported')
            evaluate(access[1], context)
        for guard in guards:
            if type(guard) is not list or len(guard) != 2 or type(guard[1]) is not bool:
                raise ValueError('Catalog guard')
            evaluate(guard[0], context)
        slots = materialize(recipe, context)
        if slots is None: raise ValueError('Training guards do not match')
        index.add(code_hash, selector, context, recipe, slots)
    index.frozen = True
    return index


class RecipePrefetcher:
    def __init__(self, edge, index=None, *, budget=60, max_slots=4, capacity=16):
        for value, low, high in [(budget, 0, 120), (max_slots, 1, 16), (capacity, 1, 64)]:
            if type(value) is not int or not low <= value <= high: raise ValueError('Prefetch bound')
        self.edge, self.index = edge, index
        self.budget, self.max_slots, self.capacity = budget, max_slots, capacity
        self.lock = threading.Lock(); self.counts = Counter()
        self.pending = set(); self.starts = deque(); self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='synafly-prefetch')

    def stats(self):
        with self.lock:
            return {**self.counts, 'pending': len(self.pending), 'enabled': self.index is not None and self.budget > 0}

    def schedule(self, call, block):
        if self.index is None or not self.budget or not block.cacheable:
            return
        data = bytes.fromhex(call.get('data', call.get('input', '0x'))[2:])
        if not 4 <= len(data) <= 256:
            with self.lock: self.counts['ineligible'] += 1
            return
        key = hashlib.sha256(encode([call, block.wire()])).digest()
        with self.lock:
            now = time.monotonic()
            while self.starts and self.starts[0] <= now - 60: self.starts.popleft()
            if self.closed or key in self.pending or len(self.pending) >= self.capacity or len(self.starts) >= self.budget:
                self.counts['skipped_budget_or_duplicate'] += 1
                return
            self.pending.add(key); self.starts.append(now); self.counts['scheduled'] += 1
            # Submit under the lock so close() cannot race a late submission.
            self.pool.submit(self._run, key, call, block, data)

    def _run(self, key, call, block, data):
        try:
            with self.edge.origin.lane('prefetch'):
                code = self.edge.read('eth_getCode', [call['to'], block.wire()])
                # Code hashing and recipe work are bounded independently of RPC response size.
                if len(code) > 2 + 4096*2:
                    with self.lock: self.counts['code_too_large'] += 1
                    return
                code_hash = '0x' + keccak256(bytes.fromhex(code[2:])).hex()
                context = call_context(data, int(call.get('from', '0x' + '00'*20), 16), int(call.get('value', '0x0'), 16))
                prediction = self.index.query('flyhash', code_hash, data[:4].hex(), context)
                with self.lock:
                    self.counts['queries'] += 1
                    self.counts['candidates'] += prediction['candidates']
                    self.counts['abstentions'] += int(prediction['abstained'])
                for slot in prediction['slots'][:self.max_slots]:
                    with self.lock: self.counts['slot_attempts'] += 1
                    self.edge.read('eth_getStorageAt', [call['to'], hex(slot), block.wire()])
                    with self.lock: self.counts['slot_reads_completed'] += 1
                with self.lock: self.counts['slots_omitted_by_budget'] += max(0, len(prediction['slots']) - self.max_slots)
        except (RpcError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            with self.lock: self.counts['errors'] += 1
        except Exception:
            # Advisory worker failure is visible, but cannot corrupt foreground data.
            with self.lock: self.counts['internal_errors'] += 1
        finally:
            with self.lock: self.pending.discard(key)

    def close(self):
        with self.lock: self.closed = True
        self.pool.shutdown(wait=True)
