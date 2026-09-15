#!/usr/bin/env python3
"""Extract a declared, biased research sample; never invent missing connections."""
import argparse, collections, hashlib, io, json
from pathlib import Path
import pyarrow as pa
import pyarrow.feather as feather

URL = 'https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome/connectome-weights-male-cns-v1.0-minconf-0.5.feather?generation=1780494887545976'
PREFIX_LENGTH = 521800
PREFIX_SHA256 = '330ec2531a258e3da3b679e332704af83ce3d1d737c77d520848ea43d51cbf76'
ANNOTATIONS_SHA256 = '2177e246113e4cfbf1e7772ec37c6da1955ff22e8063d0b1f833101f99a9a3b2'

def extract(prefix, annotations, out):
    raw=Path(prefix).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PREFIX_SHA256: raise ValueError('Source prefix checksum mismatch')
    if len(raw)!=PREFIX_LENGTH or raw[:8]!=b'ARROW1\x00\x00': raise ValueError('Wrong source prefix')
    with Path(annotations).open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=ANNOTATIONS_SHA256: raise ValueError('Annotations checksum mismatch')
    reader=pa.ipc.open_stream(io.BytesIO(raw[8:])); batch=reader.read_next_batch()
    if batch.num_rows!=65536 or batch.schema.names!=['body_pre','body_post','weight']: raise ValueError('Unexpected batch')
    ann=feather.read_table(annotations,columns=['bodyId','class','superclass']).to_pydict()
    valid={i for i,c,s in zip(ann['bodyId'],ann['class'],ann['superclass']) if 'glia' not in str(c).lower()+' '+str(s).lower() and 'artifact' not in str(c).lower()+' '+str(s).lower()}
    d=batch.to_pydict(); rows=[(a,b,w) for a,b,w in zip(d['body_pre'],d['body_post'],d['weight']) if a in valid and b in valid and a!=b and w>0]
    neighbors=collections.defaultdict(set)
    for a,b,w in rows: neighbors[a].add(b);neighbors[b].add(a)
    selected=[]; seen=set(); queue=collections.deque([min(neighbors)])
    while queue and len(selected)<256:
        node=queue.popleft()
        if node in seen: continue
        seen.add(node);selected.append(node);queue.extend(sorted(neighbors[node]-seen))
    ids=sorted(selected); index={v:i for i,v in enumerate(ids)}
    edges=sorted([index[a],index[b],w] for a,b,w in rows if a in index and b in index)
    graph={'schema':'synafly.graph.v1','nodes':[str(i) for i in ids],'edges':edges}
    graph_bytes=(json.dumps(graph,sort_keys=True,separators=(',',':'))+'\n').encode()
    out=Path(out);out.mkdir(parents=True,exist_ok=True);(out/'malecns-sample.json').write_bytes(graph_bytes)
    manifest={'dataset':'male-cns:v1.0','license':'CC-BY-4.0','source_url':URL,'generation':'1780494887545976','source_range':'bytes 0-521799','range_sha256':hashlib.sha256(raw).hexdigest(),'full_source_hash_verified':False,'annotations_sha256':ANNOTATIONS_SHA256,'records_read':65536,'nodes':len(ids),'edges':len(edges),'sample_file_sha256':hashlib.sha256(graph_bytes).hexdigest(),'sampling':'First complete Arrow record batch; remove nonannotated/glial/artifact/self-loop edges; undirected BFS from smallest available body ID with ascending neighbors, first256 nodes; retain observed directed weighted edges among selected nodes.','limitations':['The first batch is enriched for high-weight edges. This is not a representative or complete induced subgraph.','Later batches may contain additional edges between these neurons. Missing edges are not filled.','Synapse counts are real; model signs, dynamics and parameters are illustrative. No claim of whole-brain emulation.'],'attribution':'MaleCNS collaboration: FlyEM / HHMI Janelia, University of Cambridge, MRC LMB and Google Research. Source and license: https://male-cns.janelia.org/download/'}
    (out/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ['nodes','edges','range_sha256','sample_file_sha256']}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('prefix');p.add_argument('annotations');p.add_argument('--out',default='data');a=p.parse_args();extract(a.prefix,a.annotations,a.out)
