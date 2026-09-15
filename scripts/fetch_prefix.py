#!/usr/bin/env python3
"""Explicit, bounded download of the pinned official source excerpt. No arbitrary URL."""
import hashlib,urllib.request
from pathlib import Path
URL='https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome/connectome-weights-male-cns-v1.0-minconf-0.5.feather?generation=1780494887545976'
EXPECTED='330ec2531a258e3da3b679e332704af83ce3d1d737c77d520848ea43d51cbf76'
request=urllib.request.Request(URL,headers={'Range':'bytes=0-521799','Accept-Encoding':'identity'})
with urllib.request.urlopen(request,timeout=30) as response:
    if response.status!=206 or response.headers.get('Content-Range')!='bytes 0-521799/1051241946' or response.headers.get('x-goog-generation')!='1780494887545976':raise SystemExit('Source range/generation mismatch')
    data=response.read(521801)
if len(data)!=521800 or hashlib.sha256(data).hexdigest()!=EXPECTED:raise SystemExit('Source integrity mismatch')
p=Path('.cache/weights-prefix.arrow');p.parent.mkdir(exist_ok=True)
if p.exists() and p.read_bytes()!=data:raise SystemExit('Existing file differs; no overwrite')
p.write_bytes(data);print('Verified pinned source excerpt: '+EXPECTED)
