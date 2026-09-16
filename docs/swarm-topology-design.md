# Swarm topology: deterministic replay and matched-policy controls

## Decision and scope

Compare exact-key lookup/cache cooperation across bounded overlays while keeping
origin attempts and peer traffic in separate units. This is a **58-role serial
simulation**, not a BSC node, HTTP concurrency test, physiological brain or dynamic
repair protocol. The useful question is whether a data-derived sparse overlay can
reduce modeled origin fallbacks and messaging against explicit alternatives.

The existing 1,000-client HTTP experiment is separate. Do not transfer its actual
socket/concurrency evidence to this model. No full-neuron matrix sweep is needed.

## Inputs and attribution

The report embeds a complete small aggregation imported from locally available
MaleCNS v1.0 data: 58 `superclass × somaSide` groups from 165,122 annotated `Traced`
CNS neurons, 25,563,096 retained positive non-self connection records and 1,229
cross-group pairs. These are classification groups, **not 256 functional groups,
neuropils, servers or the original neuron adjacency graph**.

Original observations: [MaleCNS / HHMI Janelia download page](https://male-cns.janelia.org/download/).
Attribution: MaleCNS collaboration, FlyEM / HHMI Janelia, University of Cambridge,
MRC LMB and Google Research. The embedded derived aggregation is **CC BY 4.0**;
research code remains covered by the repository's MIT license. Grouping, endpoint
filtering and engineered overlay changes are recorded, not implied to be original
biological anatomy.

The aggregation carries original file size, SHA-256/MD5 and extractor provenance
from its prior local conversion. This release pins and validates the exported
aggregation, its counts and topology construction. It does not download or rescan
the full source, and does not include the earlier full-source extractor. Four
opaque SHA-256 cache keys preserve the original owner mapping; address-looking
source labels are not evidence of DEX activity and are not needed in this export.
The submitted script/report hashes and ten original rows are retained for audit.

## Overlay construction

- Region placement: shuffle 58 role IDs with seed 0, assign round-robin to four
  **synthetic** regions. Region costs are assumed 1 within / 8 across regions.
- **Affinity:** successor/predecessor ring backbone, then largest outgoing
  aggregated synapse weights, with deterministic fill to six outgoing arcs.
  All engineered backbone/fill arcs and measured-overlap counts are recorded.
- **Geographic:** same ring backbone and degree budget, with region-based choices.
- **Chorded ring:** offsets `+1, -1, +N//4, -N//4, +N//2, +2`; six arcs per role.
  This is not a standards-compliant DHT implementation.
- **Flood:** a complete directed graph. Each uncached ingress lookup probes every
  eligible peer. This is an intentionally naive all-peer query baseline.
- **Rewired controls:** seeds 11 and 23, preserving the affinity graph's in/out
  degree sequence and outgoing edge weights, using the existing public rewirer.
- **Isolated:** no peer lookup; same local cache capacity as other modes.

Owner = first 32 bits of an opaque key digest modulo 58. Owner assignment and
traffic are identical across strategies. Routed lookups use global least-cost
Dijkstra paths with lexicographic ties, computed without failures. No discovery,
directory maintenance or distributed next-hop convergence is simulated.

The hop statistic is over **all 58 × 57 ordered distinct endpoint pairs**, using
these region-weighted routes. It is not minimum-hop BFS, request-weighted routing,
physical latency, or a measurement of paths during the failure workload.

## Policy isolation and source replay

Every request finishes before the next starts. There are **zero coalesced waiters**;
the original transient in-flight map could never merge overlapping requests.
The standalone model therefore does not advertise concurrency or single-flight.
Cached values are deterministic synthetic tokens checked by the driver, not EVM
responses or authenticated blockchain state.

Every cache write is bounded to 16 entries, including ingress copies and origin
fallbacks. This fixes a latent unbounded-write bug in the submitted script; the
original four-key workload never exceeded the bound, so its ten rows remain exact.

Two policies are kept separate:

1. **Historical replay:** flood peer hits do not populate the requesting cache,
   while routed hits do. Failed peers are omitted from flood fanout using an
   ideal live-set view; a routed path containing any failed role falls back before
   charging any path bytes. Preserve this asymmetric accounting to audit the
   supplied numbers, not to recommend it as a fair production comparison.
2. **Matched policy:** flood peer hits also fill the requester cache. Flood probes
   charge each attempted request, including failed peers, and replies only from
   live peers. Routed probes charge forward hops through the first failed node;
   successful paths charge a full return. Failed-path fallback still does not
   reroute. Timeout control packets/latency and failure detection costs are absent.

These changes do not add an alternate-path search or biological self-healing.
Requests assigned to a failed ingress take a modeled **external caller's direct
origin fallback**, not execution by a crashed process. No real availability claim
follows from that assumption.

## Fixed experiment matrix

Inputs are locked before recording results. No seed is selected after looking at
an outcome:

- Source replay: traffic seed 42, 2,048 choices among four pinned synthetic keys;
  five original strategies; healthy and fixed-five-failure cases (10 rows).
- Five failures are sampled **after** generating that traffic, matching the source.
  The label "10% Churn" is historical only: **5/58 = 8.620690%**, fixed before run.
- Matched controls: seeds **42–46**, all seven strategies, healthy / five failures /
  six failures (105 rows). The sixth failed role is drawn from the remaining live
  roles after the original five; **6/58 = 10.344828%**, not exactly 10% either.
- Negative workload controls: seed 42, seven strategies, no failures; (a) 2,048
  unique keys and (b) block-context changes every 64 requests (14 rows).

Cache state resets per trial. Faults are static within a trial; no joining,
recovery, temporal churn, Byzantine payload or queueing process is modeled.

## Accounting and interpretation

Peer request/reply sizes are **assumed 128/180 bytes per hop**. They are not
serialized JSON captures, transport overhead measurements or bandwidth rates.
Origin reads are simulation events, not physical NVMe reads. The tier scenario is
`200 × origin_reads + assumed_peer_bytes / 1,000,000`, with 100/500 sensitivity;
not dollars, a verified price ratio, energy or a fleet-capacity forecast.

Healthy complete-graph adjacency has `N(N-1)` arcs, but each uncached flood lookup
has O(N) peer exchanges. Q such lookups cost O(QN); O(N²) workload traffic follows
only if Q itself scales with N. This is not a proof about all gossip protocols.
[libp2p GossipSub](https://github.com/libp2p/specs/blob/master/pubsub/gossipsub/gossipsub-v1.1.md)
has mesh/fanout and adaptive dissemination rules; it is not implemented by the
naive full-graph baseline here.

Report comparisons both against flooding and against the sparse controls. Do not
call affinity globally optimal if another strategy has fewer origin events or a
lower declared score. The initial three sparse strategies and the broader rewired
controls must remain distinguishable. Keep all seeds, failures and regressions.

## Reproduction and containment

The report includes the inputs; no private research module or new runtime
dependency is required. The script's default audits an exact deterministic replay;
`--out` writes a separate capture. All implementation/source hashes are recorded.
Tests check provenance, topology construction, cache bounds, failure-prefix traffic,
policy asymmetry, exact historical rows, negative controls and report tampering.
No public RPC, transaction, production deployment or client configuration change.
