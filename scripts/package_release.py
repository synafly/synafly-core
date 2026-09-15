#!/usr/bin/env python3
"""Build a reviewed source ZIP; no commits, network calls or GitHub publication."""
import hashlib,json,re,tomllib,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES={'synafly_lab','tests','scripts','contracts','test','docs','data','results','abi','deployments','.github'}
ROOT_FILES={'README.md','LICENSE','SECURITY.md','RELEASING.md','AGENTS.md','.gitignore','pyproject.toml','foundry.toml','requirements-data.txt'}
SUFFIXES={'.py','.sol','.md','.json','.txt','.yml','.yaml','.svg','.mmd'}

def selected():
    paths=[]
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if p.is_symlink():
            if rel.parts[0] in DIRECTORIES:raise ValueError('Unexpected symlink: '+str(rel))
            continue
        if not p.is_file() or set(rel.parts)&{'.git','.cache','.local','.codex','__pycache__','node_modules','out','cache'}:continue
        if str(rel) in ROOT_FILES or rel.parts[0] in DIRECTORIES and p.suffix in SUFFIXES:paths.append(p)
    return sorted(paths)

def main():
    paths=selected();issues=[]
    for p in paths:
        rel=str(p.relative_to(ROOT));text=p.read_text()
        if re.search(r'/Users/[A-Za-z0-9._-]+/',text) or re.search(r'-----BEGIN (?:RSA )?PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]+',text):issues.append({'type':'private-path-or-key-marker','path':rel})
        if re.search(r'gh[pousr]_[A-Za-z0-9]{30,}|sk-proj-[A-Za-z0-9_-]{20,}',text):issues.append({'type':'credential-shaped-string','path':rel})
        if p.stat().st_size>4*1024*1024:issues.append({'type':'unexpected-large-file','path':rel})
    if issues:raise SystemExit(json.dumps(issues))
    source={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.relative_to(ROOT).parts[0]!='results'}
    (ROOT/'results/source-manifest.json').write_text(json.dumps({'source_sha256':source,'scope':'Source/data/docs; results excluded from this source fingerprint','screening':'Limited literal credential/path checks; not a security audit'},indent=2)+'\n')
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Release version')
    paths=selected();out=ROOT/'dist';out.mkdir(exist_ok=True);target=out/('synafly-lab-v'+version+'.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in paths:
            entry=zipfile.ZipInfo('synafly-lab/'+str(p.relative_to(ROOT)),date_time=(1980,1,1,0,0,0))
            entry.create_system=3;entry.external_attr=0o100644<<16;entry.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(entry,p.read_bytes())
    print(json.dumps({'archive':str(target.relative_to(ROOT)),'files':len(paths),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'screening_issues':issues}))
if __name__=='__main__':main()
