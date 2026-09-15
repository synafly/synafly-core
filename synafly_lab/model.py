"""Integer toy dynamics: real graph structure, explicitly nonvalidated physiology."""
from dataclasses import dataclass
import hashlib
from .canonical import Invalid,canonical,digest,integer,keys

DEFAULT_MODEL={'schema':'synafly.integer-lif.v1','leak_num':7,'leak_den':8,'threshold':1024,'synapse_gain':8,'refractory_ticks':2,'max_potential':1000000}

@dataclass(frozen=True)
class Circuit:
    graph: dict
    parameters: dict
    def __post_init__(self):
        keys(self.graph,['schema','nodes','edges'])
        if self.graph['schema']!='synafly.graph.v1': raise Invalid('Graph schema')
        nodes=self.graph['nodes']; edges=self.graph['edges']
        if type(nodes) is not list or not 1<=len(nodes)<=2048 or any(type(n) is not str or not n.isascii() or not n.isdigit() or str(int(n))!=n for n in nodes): raise Invalid('Node identifiers')
        if nodes!=sorted(set(nodes),key=int): raise Invalid('Nodes must be unique and sorted')
        if type(edges) is not list or len(edges)>50000: raise Invalid('Edge limit')
        seen=set()
        for row in edges:
            if type(row) is not list or len(row)!=3: raise Invalid('Edge')
            a,b,w=row;integer(a,0,len(nodes)-1);integer(b,0,len(nodes)-1);integer(w,1,1000000)
            if a==b or (a,b) in seen: raise Invalid('Duplicate/self edge')
            seen.add((a,b))
        if edges!=sorted(edges): raise Invalid('Edges must be sorted')
        p=self.parameters;keys(p,DEFAULT_MODEL)
        if p['schema']!=DEFAULT_MODEL['schema']: raise Invalid('Model version')
        integer(p['leak_den'],1,1024);integer(p['leak_num'],0,p['leak_den'])
        integer(p['threshold'],1,100000);integer(p['synapse_gain'],1,1024)
        integer(p['refractory_ticks'],0,100);integer(p['max_potential'],p['threshold'],10000000)
        canonical(self.graph);canonical(p)
        outgoing=[[] for _ in nodes]
        for a,b,w in edges:outgoing[a].append((b,w))
        object.__setattr__(self,'_outgoing',tuple(tuple(row) for row in outgoing))
    @property
    def n(self): return len(self.graph['nodes'])
    def initial(self): return {'tick':0,'potential':[0]*self.n,'refractory':[0]*self.n,'spikes':[],'total_spikes':0}
    def step(self,state,inputs):
        if type(inputs) is not list or len(inputs)>self.n: raise Invalid('Input limit')
        drive=[0]*self.n;last=-1
        for row in inputs:
            if type(row) is not list or len(row)!=2: raise Invalid('Input pair')
            node,value=row;integer(node,0,self.n-1);integer(value,0,10000)
            if node<=last: raise Invalid('Inputs must be unique and sorted')
            last=node;drive[node]=value
        p=self.parameters
        # Only previously firing neurons transmit over their outgoing connections.
        for a in state['spikes']:
            for b,w in self._outgoing[a]:drive[b]+=w*p['synapse_gain']
        potentials=[];refractory=[];spikes=[]
        for i in range(self.n):
            if state['refractory'][i]>0: v=0;r=state['refractory'][i]-1
            else:
                v=min(p['max_potential'],state['potential'][i]*p['leak_num']//p['leak_den']+drive[i]);r=0
                if v>=p['threshold']: spikes.append(i);v=0;r=p['refractory_ticks']
            potentials.append(v);refractory.append(r)
        return {'tick':state['tick']+1,'potential':potentials,'refractory':refractory,'spikes':spikes,'total_spikes':state['total_spikes']+len(spikes)}

def stimulus(seed,tick,n):
    raw=hashlib.sha256((seed+':'+str(tick)).encode()).digest()
    return [[int.from_bytes(raw[:4],'big')%n,1400]]
