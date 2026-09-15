"""Checkpoints are accepted only after deterministic transition replay."""
from copy import deepcopy
from .canonical import Invalid,digest,keys,canonical
from .model import Circuit,DEFAULT_MODEL
ZERO='0'*64
MAX_FRAMES=16

class Run:
    def __init__(self,graph,seed='synafly-research-v1',parameters=None):
        if type(seed) is not str or not 1<=len(seed)<=128: raise Invalid('Seed')
        self.circuit=Circuit(deepcopy(graph),deepcopy(DEFAULT_MODEL if parameters is None else parameters))
        self.spec={'schema':'synafly.run.v1','graph':self.circuit.graph,'model':self.circuit.parameters,'seed':seed}
        self.id=digest(self.spec);self.graph_hash=digest(graph);self.model_hash=digest(self.circuit.parameters)
    def _payload(self,state,inputs,sequence,parent):
        return {'schema':'synafly.checkpoint.v1','run_id':self.id,'graph_hash':self.graph_hash,'model_hash':self.model_hash,'sequence':sequence,'tick':state['tick'],'parent':parent,'inputs':deepcopy(inputs),'input_hash':digest(inputs),'state':state,'state_hash':digest(state)}
    def genesis(self): return self._payload(self.circuit.initial(),[],0,ZERO)
    def advance(self,parent,inputs):
        if type(inputs) is not list or not 1<=len(inputs)<=MAX_FRAMES: raise Invalid('Checkpoint frame limit')
        if parent['sequence']>=2047: raise Invalid('Research lineage limit reached')
        state=deepcopy(parent['state'])
        for frame in inputs: state=self.circuit.step(state,frame)
        return self._payload(state,inputs,parent['sequence']+1,digest(parent))
    def verify(self,payload,parent=None):
        if type(payload) is not dict: raise Invalid('Checkpoint object')
        if parent is None: expected=self.genesis()
        else: expected=self.advance(parent,payload.get('inputs'))
        if canonical(payload)!=canonical(expected): raise Invalid('Checkpoint does not match deterministic replay')
        return digest(payload)
