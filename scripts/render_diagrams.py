#!/usr/bin/env python3
"""Generate portable SVG + Mermaid diagrams without external services or local metadata."""
from pathlib import Path
from html import escape
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/diagrams'

def render(name,title,subtitle,nodes,edges,footer):
    width,height=1440,760
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>', '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#f0b90b"/></marker></defs>', '<rect width="1440" height="760" rx="20" fill="#0b0e11"/>', '<style>text{font-family:Arial,Helvetica,sans-serif} .title{font-size:38px;font-weight:700;fill:#f4f4ee}.sub{font-size:20px;fill:#b6c0c9}.label{font-size:22px;font-weight:700;fill:#f0b90b}.body{font-size:18px;fill:#e4e8eb}.note{font-size:16px;fill:#a4afb9}.edge{fill:none;stroke:#f0b90b;stroke-width:2;marker-end:url(#arrow)}</style>', f'<text class="title" x="48" y="66">{escape(title)}</text>',f'<text class="sub" x="48" y="106">{escape(subtitle)}</text>']
    mmd=['flowchart LR']; positions={n['id']:n for n in nodes}
    for n in nodes:
        x,y,w,h=n['box'];dash=' stroke-dasharray="8 6"' if n.get('future') else ''
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#11181f" stroke="{ "#768492" if n.get("future") else "#e6b526"}" stroke-width="1.7"{dash}/>')
        svg.append(f'<text class="label" x="{x+20}" y="{y+36}">{escape(n["title"])}</text>')
        for i,line in enumerate(n['lines']):svg.append(f'<text class="body" x="{x+20}" y="{y+69+i*26}">{escape(line)}</text>')
        label=n['title']+' | '+' | '.join(n['lines']);mmd.append(f'  {n["id"]}["{label}"]')
        if n.get('future'):mmd.append(f'  style {n["id"]} stroke-dasharray:5 5')
    for e in edges:
        a,b,label,points=e;svg.append(f'<polyline class="edge" points="{points}"/>')
        mmd.append(f'  {a} -->|"{label}"| {b}')
    for i,line in enumerate(footer):svg.append(f'<text class="note" x="48" y="{690+i*26}">{escape(line)}</text>')
    svg.append('</svg>');OUT.mkdir(parents=True,exist_ok=True);(OUT/(name+'.svg')).write_text('\n'.join(svg)+'\n');(OUT/(name+'.mmd')).write_text('\n'.join(mmd)+'\n')

render('architecture','SynaFly Core Lab · State continuity','Implemented research components; public-chain deployment is a separate milestone.',[
 {'id':'graph','title':'Real graph excerpt','lines':['MaleCNS v1.0','256 nodes · 605 edges','Pinned data provenance'],'box':[48,180,260,158]},
 {'id':'model','title':'Integer state model','lines':['Graph-weighted dynamics','Explicit inputs + parameters','Illustrative physiology'],'box':[370,180,280,158]},
 {'id':'checkpoint','title':'Checkpoint engine','lines':['Canonical JSON + SHA-256','Parent-linked hash chain','Full replay before acceptance'],'box':[715,180,310,158]},
 {'id':'keepers','title':'Keeper replicas','lines':['Separate processes / stores','Peer pulls + operator control','Fast-forward only'],'box':[1090,180,302,158]},
 {'id':'registry','title':'ContinuityRegistry','lines':['2-of-3 witness quorum','EIP-191 personal-sign','Tested on local EVM'],'box':[715,462,310,158]},
 {'id':'publicChain','title':'Public BSC anchors','lines':['Target deployment','Independent witness operators','Not yet deployed'],'box':[1090,462,302,158],'future':True}
],[('graph','model','weights','308,258 365,258'),('model','checkpoint','state and inputs','650,258 710,258'),('checkpoint','keepers','replay-verified copy','1025,258 1085,258'),('checkpoint','registry','operator preparation and external attestations','870,338 870,457'),('registry','publicChain','future public deployment','1025,540 1085,540')],['Keepers retain the recoverable bytes. The registry stores commitments; it does not execute the model.','Solid: implemented local mechanisms. Dashed: future public-network milestone.'])

render('recovery','Keeper failure → verified recovery','Reference experiment: three processes on one host, not geographical decentralization.',[
 {'id':'a','title':'1 · Keeper A','lines':['Advance explicit inputs','Store checkpoint history'],'box':[48,170,280,130]},
 {'id':'b','title':'2 · Keeper B','lines':['Pull and replay-check history','Keep an independent copy'],'box':[430,170,340,130]},
 {'id':'stopA','title':'3 · Original stops','lines':['Test harness terminates A','No further access to A'],'box':[885,170,420,130]},
 {'id':'c','title':'4 · Fresh Keeper C','lines':['Fetch graph / spec from B','Pull complete missing history'],'box':[885,450,420,145]},
 {'id':'replay','title':'5 · Verify every step','lines':['Check run, model and parent','Recompute the complete state','Reject tampered or forked data'],'box':[430,450,340,145]},
 {'id':'resume','title':'6 · Continue','lines':['Atomic fast-forward import','Tick 72 matches uninterrupted','execution byte for byte'],'box':[48,450,320,145]}
],[('a','b','replicate','328,235 425,235'),('b','stopA','after replication','770,235 880,235'),('stopA','c','fresh process','1095,300 1095,445'),('b','c','surviving peer supplies bytes','600,300 600,373 1000,373 1000,445'),('c','replay','verify history','885,522 775,522'),('replay','resume','valid fast-forward','430,522 373,522')],['The demonstrated result is recovery after failure. Automatic WAN self-healing is not claimed.','A retained graph, model and at least one valid copy are required; a hash alone cannot restore missing data.'])

render('bssr-gates','BSSR · Proposed edge layer and evidence gates','Research blueprint. This RPC path is not implemented by the current keeper release.',[
 {'id':'client','title':'Read-only client','lines':['Bounded method surface','Explicit block context'],'box':[48,170,250,130],'future':True},
 {'id':'cache','title':'Context + coalescing','lines':['Exact request identity','Freshness / reorg rules'],'box':[365,170,300,130],'future':True},
 {'id':'route','title':'Sparse edge routing','lines':['Peer selection + fallback','Matched nonbiological baseline'],'box':[730,170,330,130],'future':True},
 {'id':'origin','title':'BSC origin RPC','lines':['Unchanged PoSA / EVM','Measured upstream work'],'box':[1125,170,267,130],'future':True},
 {'id':'g1','title':'Gate 1 · Local pass','lines':['Deterministic continuity','Peer recovery evidence'],'box':[48,465,300,140]},
 {'id':'g2','title':'Gate 2 · Local pass','lines':['Witness-quorum registry','Local EVM receipts'],'box':[398,465,300,140]},
 {'id':'g3','title':'Gate 3 · Pending','lines':['Role-correct RPC prototype','Adversarial correctness tests'],'box':[748,465,300,140],'future':True},
 {'id':'g4','title':'Gate 4 · Pending','lines':['Independent WAN tests','Full cost / resource accounting'],'box':[1098,465,294,140],'future':True}
],[('client','cache','proposed request path','298,235 360,235'),('cache','route','proposed peer lookup','665,235 725,235'),('route','origin','cache miss / fallback','1060,235 1120,235'),('g1','g2','prerequisite mechanisms','348,535 393,535'),('g2','g3','next evidence gate','698,535 743,535'),('g3','g4','before performance claims','1048,535 1093,535')],['Fewer origin requests do not imply the same percentage reduction in total node costs.','Economic inputs are scenarios until supported by measured workloads and complete accounting.'])
print('Rendered three SVG diagrams and their Mermaid sources.')
