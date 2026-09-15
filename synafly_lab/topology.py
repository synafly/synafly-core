"""Controlled connectivity experiments, not an implemented WAN routing network."""
from collections import deque
import copy
import hashlib
import random
from .canonical import Invalid, integer
from .model import Circuit, DEFAULT_MODEL

def rewire(graph, seed):
    """Directed swaps preserve in/out degree and outgoing weights, not in-strength."""
    Circuit(graph, DEFAULT_MODEL)
    result = copy.deepcopy(graph)
    edges = result['edges']
    if len(edges) < 2: return result, 0
    pairs = {(a,b) for a,b,_ in edges}
    rng, swaps = random.Random(seed), 0
    for _ in range(len(edges) * 20):
        i,j = rng.sample(range(len(edges)),2)
        a,b,w = edges[i];c,d,v = edges[j]
        if a==c or b==d or a==d or c==b or (a,d) in pairs or (c,b) in pairs: continue
        pairs.remove((a,b));pairs.remove((c,d));pairs.add((a,d));pairs.add((c,b))
        edges[i]=[a,d,w];edges[j]=[c,b,v];swaps+=1
    result['edges']=sorted(edges)
    return result, swaps

def conventional_overlay(graph):
    """De Bruijn-style edges plus deterministic ring links; same node/edge counts.

    This control does not preserve the biological graph's degree or weight profile.
    It is a practical topology comparator, separate from degree-matched rewiring.
    """
    Circuit(graph, DEFAULT_MODEL)
    n, count = len(graph['nodes']), len(graph['edges'])
    pairs = set()
    for bit in [0,1]:
        for source in range(n):
            target=(source*2+bit)%n
            if source != target and len(pairs)<count: pairs.add((source,target))
    for offset in range(1,n):
        for source in range(n):
            if len(pairs)<count: pairs.add((source,(source+offset)%n))
        if len(pairs)==count: break
    assert len(pairs)==count
    return {'schema':graph['schema'],'nodes':list(graph['nodes']),
            'edges':[[a,b,1] for a,b in sorted(pairs)]}

def owners(key, node_count, replicas=3):
    integer(node_count,1,2048);integer(replicas,1,node_count)
    if type(key) is not str or not key or len(key)>256: raise Invalid('Key bound')
    # A synthetic, graph-independent ownership map, identical for every strategy.
    ranked=sorted(range(node_count),key=lambda i:hashlib.sha256((key+':'+str(i)).encode()).digest())
    return tuple(ranked[:replicas])

class Overlay:
    def __init__(self,graph):
        Circuit(graph,DEFAULT_MODEL)
        self.n=len(graph['nodes'])
        adjacent=[[] for _ in range(self.n)]
        for a,b,_ in graph['edges']:adjacent[a].append(b)
        self.adjacent=tuple(tuple(sorted(row)) for row in adjacent)
    def _arguments(self,source,targets,failed,budget,hops):
        integer(source,0,self.n-1);integer(budget,1,self.n);integer(hops,1,self.n)
        targets=tuple(targets);failed=frozenset(failed)
        if not targets or len(targets)>self.n or len(set(targets))!=len(targets):raise Invalid('Target set')
        for node in [*targets,*failed]:integer(node,0,self.n-1)
        return targets,failed
    def search(self,source,targets,failed=(),budget=None,hops=None):
        """Budgeted BFS over live vertices; failed contacts consume the budget.

        One abstract lookup contact is a request attempt, not a byte, millisecond or
        real WAN packet. The entry vertex is local; source failure falls to origin.
        Synaptic weights are deliberately ignored to isolate adjacency structure.
        """
        budget=min(32,self.n) if budget is None else budget
        hops=min(8,self.n) if hops is None else hops
        targets,failed=self._arguments(source,targets,failed,budget,hops)
        if source in failed:return {'found':False,'contacts':0,'failed_contacts':0,'hops':-1,'entry_failed':True}
        if source in targets:return {'found':True,'contacts':0,'failed_contacts':0,'hops':0,'entry_failed':False}
        queue=deque([(source,0)]);seen={source};contacts=bad=0
        while queue:
            node,depth=queue.popleft()
            if depth>=hops:continue
            for other in self.adjacent[node]:
                if other in seen:continue
                if contacts>=budget:return {'found':False,'contacts':contacts,'failed_contacts':bad,'hops':-1,'entry_failed':False}
                seen.add(other);contacts+=1
                if other in failed:bad+=1;continue
                if other in targets:return {'found':True,'contacts':contacts,'failed_contacts':bad,'hops':depth+1,'entry_failed':False}
                queue.append((other,depth+1))
        return {'found':False,'contacts':contacts,'failed_contacts':bad,'hops':-1,'entry_failed':False}
    def direct(self,source,targets,failed=()):
        """Directory-aware direct-owner reference; NOT constrained by graph edges."""
        targets,failed=self._arguments(source,targets,failed,self.n,self.n)
        if source in failed:return {'found':False,'contacts':0,'failed_contacts':0,'hops':-1,'entry_failed':True}
        if source in targets:return {'found':True,'contacts':0,'failed_contacts':0,'hops':0,'entry_failed':False}
        contacts=0
        for target in targets:
            contacts+=1
            if target not in failed:return {'found':True,'contacts':contacts,'failed_contacts':contacts-1,'hops':1,'entry_failed':False}
        return {'found':False,'contacts':contacts,'failed_contacts':contacts,'hops':-1,'entry_failed':False}
    def connectivity(self):
        total=0
        for source in range(self.n):
            seen={source};queue=deque([source])
            while queue:
                for node in self.adjacent[queue.popleft()]:
                    if node not in seen:seen.add(node);queue.append(node)
            total+=len(seen)-1
        return {'reachable_ordered_pairs':total,'possible_ordered_pairs':self.n*(self.n-1),
                'zero_out_degree':sum(not row for row in self.adjacent),
                'max_out_degree':max(map(len,self.adjacent))}
