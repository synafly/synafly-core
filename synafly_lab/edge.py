"""Bounded immutable-state cache and single-flight read coalescing."""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
import math
import threading
import time
from .rpc import RpcError, cache_key, parse_read, validate_result

class ReadEdge:
    def __init__(self, origin, cache_entries=128, cache_bytes=1048576, ttl=60.0,
                 coalesce=True, max_inflight=8, clock=time.monotonic):
        for value, low, high in [(cache_entries, 0, 4096), (cache_bytes, 0, 16777216), (max_inflight, 1, 16)]:
            if type(value) is not int or not low <= value <= high:
                raise RpcError(-32602, 'Edge resource bound')
        if type(coalesce) is not bool or type(ttl) not in (int, float) or not math.isfinite(ttl) or not 0 < ttl <= 3600:
            raise RpcError(-32602, 'Edge cache policy')
        self.origin, self.identity = origin, origin.identity
        self.cache_entries, self.cache_bytes, self.ttl = cache_entries, cache_bytes, ttl
        self.coalesce, self.clock = coalesce, clock
        self._cache, self._flights = OrderedDict(), {}
        self._bytes = 0
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._metrics = {k: 0 for k in ['read_requests', 'pinned_requests', 'bypassed_requests',
            'cache_hits', 'cache_misses', 'coalesced_waiters', 'origin_reads', 'origin_errors',
            'evictions', 'capacity_rejections']}
    def stats(self):
        with self._lock:
            return {**self._metrics, 'cache_entries': len(self._cache),
                    'cache_value_bytes': self._bytes, 'inflight_keys': len(self._flights)}
    def _cached(self, key):
        entry = self._cache.get(key)
        if entry is None: return None
        value, expires, size = entry
        if self.clock() >= expires:
            del self._cache[key]
            self._bytes -= size
            return None
        self._cache.move_to_end(key)
        return value
    def _store(self, key, value):
        size = len(value.encode())
        if not self.cache_entries or size > self.cache_bytes: return
        if key in self._cache:
            _, _, removed = self._cache.pop(key)
            self._bytes -= removed
        while self._cache and (len(self._cache) >= self.cache_entries or self._bytes + size > self.cache_bytes):
            _, (_, _, removed) = self._cache.popitem(last=False)
            self._bytes -= removed
            self._metrics['evictions'] += 1
        self._cache[key] = (value, self.clock() + self.ttl, size)
        self._bytes += size
    def _origin_read(self, request, reserved=False):
        if not reserved and not self._slots.acquire(blocking=False):
            with self._lock: self._metrics['capacity_rejections'] += 1
            raise RpcError(-32005, 'Edge concurrent-read capacity reached')
        try:
            with self._lock: self._metrics['origin_reads'] += 1
            return validate_result(request.method, self.origin.call(request.method, request.wire_params()))
        except Exception:
            with self._lock: self._metrics['origin_errors'] += 1
            raise
        finally:
            self._slots.release()
    def read(self, method, params):
        request = parse_read(method, params)
        with self._lock:
            self._metrics['read_requests'] += 1
            self._metrics['pinned_requests' if request.block.cacheable else 'bypassed_requests'] += 1
        if not request.block.cacheable:
            return self._origin_read(request)
        key = cache_key(self.identity, request)
        with self._lock:
            value = self._cached(key)
            if value is not None:
                self._metrics['cache_hits'] += 1
                return value
            if self.coalesce and key in self._flights:
                future, owner = self._flights[key], False
                self._metrics['coalesced_waiters'] += 1
            else:
                if not self._slots.acquire(blocking=False):
                    self._metrics['capacity_rejections'] += 1
                    raise RpcError(-32005, 'Edge concurrent-read capacity reached')
                future, owner = Future(), True
                if self.coalesce: self._flights[key] = future
                self._metrics['cache_misses'] += 1
        if not owner:
            try: return future.result(timeout=self.origin.timeout + 1)
            except FutureTimeout as exc: raise RpcError(-32002, 'Coalesced read timeout') from exc
        try:
            value = self._origin_read(request, reserved=True)
            with self._lock: self._store(key, value)
            future.set_result(value)
            return value
        except Exception as exc:
            # Wake every follower, then preserve the original failure for the caller.
            future.set_exception(exc)
            raise
        finally:
            if self.coalesce:
                with self._lock: self._flights.pop(key, None)
