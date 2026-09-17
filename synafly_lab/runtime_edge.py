"""Non-invasive observation/peek adapter over the evidence-locked PR #4 ReadEdge."""
import threading
from .edge import ReadEdge
from .rpc import parse_read, cache_key


class RuntimeReadEdge(ReadEdge):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.observation=threading.local()
    def _cached(self,key):
        value=super()._cached(key)
        if value is not None:self.observation.hit=True
        return value
    def _origin_read(self,request,reserved=False):
        self.observation.forwarded=True
        return super()._origin_read(request,reserved)
    def read(self,method,params,*,with_source=False):
        self.observation.hit=False;self.observation.forwarded=False
        value=super().read(method,params)
        source='cache' if self.observation.hit else 'origin' if self.observation.forwarded else 'coalesced'
        return (value,source) if with_source else value
    def peek(self,method,params):
        request=parse_read(method,params)
        if not request.block.cacheable:return None
        with self._lock:return self._cached(cache_key(self.identity,request))
