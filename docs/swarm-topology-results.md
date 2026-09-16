# Swarm topology: reproduced positives and matched-policy limits

**Scope:** deterministic serial simulation of **58 classification/side groups**.
No BSC node, NVMe device, live DEX trace or network transport was exercised.
[Design, fixed matrix and attribution](swarm-topology-design.md) ·
[Raw inputs and complete results](../results/swarm-topology-benchmark.json) ·
[Protocol/source lock](../results/swarm-topology-protocol-lock.json).

## Findings at a glance

- The supplied ten rows reproduce exactly. Healthy affinity routing needs **4
  modeled origin reads for 2,048 queries: 99.804688% offload**, rounded to 99.8%.
- Against the original naive all-peer policy, its assumed peer bytes decrease
  **99.247123%**, rounded to 99.25%. That original comparison also changes requester
  cache refill behavior, so it is not a topology-only result.
- In the supplied seed-42, **5/58 failed-role case (8.62%)**, affinity has **250
  origin reads**, versus ring **269** and geographic **260**. Its score is
  **50,000.184184**: best among those three original sparse configurations in this
  case, not best among all strategies or all seeds.
- After matching cache refill and charging failed contacts, healthy seed-42
  affinity still uses **93.473987% fewer assumed peer bytes** than all-peer querying.
  The same three sparse configurations retain the 250 / 269 / 260 read counts.
- Broader controls do **not** establish general anatomical superiority. Across
  five seeds and three failure levels, affinity beats isolated caches in all
  15 comparisons, but beats geographic routing in only 2/15 declared-score pairs.

## 1. Original observations, faithfully reproduced

Each row serves 2,048 serial queries to four opaque pinned-state keys. "Offload"
uses one origin call per query as its denominator, not the already-cached isolated
baseline. The source's BSC/DEX wording does not make these physical reads or
PancakeSwap measurements. All sequential in-flight waiter counts are zero.

### Healthy, original policy, seed 42

| Strategy | Modeled origin reads | Offload | Peer messages | Assumed peer bytes | Score at 200/read + 1/MB |
|---|---:|---:|---:|---:|---:|
| Isolated | 232 | 88.67% | 0 | 0 | 46,400.000000 |
| Naive all-peer, no peer-hit refill | 4 | 99.80% | 229,254 | 35,305,116 | 835.305116 |
| Chorded ring | 4 | 99.80% | 1,872 | 288,288 | 800.288288 |
| Geographic | 4 | 99.80% | 1,302 | 200,508 | 800.200508 |
| MaleCNS-affinity | 4 | 99.80% | 1,726 | 265,804 | 800.265804 |

The 99.25% claim is precisely:

```text
1 - 265,804 / 35,305,116 = 99.247123% fewer assumed peer bytes
```

All cooperative strategies achieve four origin events; that part is **not an
additional biological advantage**. Relative to the stronger isolated-cache
baseline, 232 → 4 is a 98.275862% reduction. Geographic routing uses fewer bytes
than affinity in this healthy case.

### Fixed five-role failure, original policy, seed 42

| Strategy | Modeled origin reads | Offload | Peer messages | Assumed peer bytes | Score at 200/read + 1/MB |
|---|---:|---:|---:|---:|---:|
| Isolated | 401 | 80.42% | 0 | 0 | 80,200.000000 |
| Naive all-peer, no peer-hit refill | 193 | 90.58% | 189,488 | 29,181,152 | 38,629.181152 |
| Chorded ring | 269 | 86.87% | 944 | 145,376 | 53,800.145376 |
| Geographic | 260 | 87.30% | 746 | 114,884 | 52,000.114884 |
| MaleCNS-affinity | 250 | 87.79% | 1,196 | 184,184 | 50,000.184184 |

Affinity's **250 versus 269/260** is a positive result against the original ring
and geographic sparse alternatives. All-peer querying uses **189,488 modeled
messages** to obtain **193 reads**, and its declared score is also lower than
50,000.184184. It therefore remains an important trade-off/negative comparator,
not a result to hide behind the word "optimal". Message counts and byte charges
here are simulated resource proxies, not measured physical bandwidth costs.

The source label "10% Churn" is retained in historical rows for provenance only:
there are five permanently unavailable roles, **5/58 = 8.620690%**. Paths are
computed before failure; a failed path falls back to origin. No repair, reconnection,
recovery of a failed owner, or biological self-healing happens.

## 2. Matched-policy comparison

The source flood strategy did not fill the requester cache after peer hits;
routed strategies did. The source also assigned zero path bytes to a routed
failure and skipped known failed flood peers. The new matched policy fixes those
asymmetries without changing traffic, keys, owner mapping or topology.

All cache writes are now bounded, including fallback/ingress writes. That fixes a
latent bug but does not change the historical four-key results because those
trials fit within the original 16-entry bound.

### Healthy, matched policy, seed 42

| Strategy | Origin reads | Peer messages | Assumed peer bytes | Score at 200/read + 1/MB |
|---|---:|---:|---:|---:|
| Isolated | 232 | 0 | 0 | 46,400.000000 |
| All-peer with peer-hit refill | 4 | 26,448 | 4,072,992 | 804.072992 |
| Chorded ring | 4 | 1,872 | 288,288 | 800.288288 |
| Geographic | 4 | 1,302 | 200,508 | 800.200508 |
| MaleCNS-affinity | 4 | 1,726 | 265,804 | 800.265804 |
| Degree-rewired 11 | 4 | 1,386 | 213,444 | 800.213444 |
| Degree-rewired 23 | 4 | 1,452 | 223,608 | 800.223608 |

Now `1 - 265804 / 4072992 = 93.473987%`. Sparse routing retains a large messaging
benefit over unconditional all-peer querying, but **99.25% is not the fair-policy
number**. Geographic and both degree-preserving controls use fewer modeled bytes
than affinity in this case. This is a sparse-routing benefit, not proof that only
a fruit-fly-derived graph can obtain it.

### Five failures, matched policy, seed 42

| Strategy | Origin reads | Peer messages | Assumed peer bytes | Score at 200/read + 1/MB |
|---|---:|---:|---:|---:|
| Isolated | 401 | 0 | 0 | 80,200.000000 |
| All-peer with peer-hit refill | 193 | 23,108 | 3,531,072 | 38,603.531072 |
| Chorded ring | 269 | 1,220 | 180,704 | 53,800.180704 |
| Geographic | 260 | 917 | 136,772 | 52,000.136772 |
| MaleCNS-affinity | 250 | 1,370 | 206,456 | 50,000.206456 |
| Degree-rewired 11 | 278 | 979 | 145,176 | 55,600.145176 |
| Degree-rewired 23 | 250 | 1,025 | 154,600 | 50,000.154600 |

The three original sparse read counts remain unchanged. However, rewired control
23 **ties affinity at 250 reads with less traffic and a slightly lower score**.
Even in the highlighted seed, affinity is not the uniquely best sparse topology
once that control is included.

### All predeclared seeds and failure levels

The extended matched matrix includes seeds 42–46, seven strategies and fixed
failure counts 0/5/6: **105 trials**. Six failures are explicitly **10.344828%**,
not mislabeled as exactly 10%. No failed case or unfavorable seed is dropped.

Paired default-score comparisons across 15 matched cases per comparator:

| Affinity compared with | Lower score | Higher score | Equal |
|---|---:|---:|---:|
| Isolated | 15 | 0 | 0 |
| All-peer | 5 | 10 | 0 |
| Chorded ring | 7 | 8 | 0 |
| Geographic | 2 | 13 | 0 |
| Degree-rewired 11 | 4 | 11 | 0 |
| Degree-rewired 23 | 1 | 14 | 0 |

Under five failures, affinity's origin counts across the five seeds are
**250, 316, 310, 316, 323**; the geographic counts are **260, 290, 272, 284, 276**.
The original positive seed does not establish a robust origin-read advantage.
These are descriptive controlled simulations, not independent production samples
or statistical significance estimates.

## 3. Hop comparison: what 3.61 versus 4.03 means

| Overlay | Directed arcs | Mean hops on least-cost routes |
|---|---:|---:|
| Chorded ring | 348 | 4.03418028 |
| MaleCNS-affinity | 348 | 3.60586812 |
| Geographic | 348 | 2.63127647 |
| Degree-rewired 11 | 348 | 2.98185118 |
| Degree-rewired 23 | 348 | 3.05444646 |

Thus **3.61 versus 4.03 is reproduced**. The average spans every ordered distinct
pair in the healthy 58-role graph. Paths minimize the assumed region cost, not
hop count; this is neither observed client latency nor an outage-repair statistic.
The ring comparison is positive, while geographic/rewired controls have shorter
averages than affinity. Each sparse overlay has six outgoing arcs per role;
rewiring also preserves the affinity graph's incoming-degree sequence.

The affinity construction forces 116 ring-backbone arcs and eight fill arcs;
300 of its 348 final arcs also occur in the measured aggregation. These sets can
overlap. This is an engineered graph informed by the MaleCNS aggregation, not an
unmodified biological network or a full-brain simulation.

## 4. Negative workloads and complexity boundary

Fourteen additional trials retain two controls across all seven strategies:

- **2,048 distinct pinned keys:** every strategy needs 2,048 origin events. Peer
  communication adds overhead without offloading any source request.
- **Block context changes every 64 queries:** isolated caches need 1,803 origin
  events; all cooperative strategies need 128. State identity prevents reusing
  old-context tokens. This remains an abstract key/version control, not real
  chain-reorganization or canonicality verification.

The all-peer graph contains `N(N-1)` adjacency arcs; each uncached lookup fans out
to O(N) peers. For Q such misses, messaging is O(QN), becoming O(N²) when Q scales
with N. No node-count scaling sweep was run. This does **not** establish that
GossipSub, bounded-fanout gossip, DHTs or BSC's actual P2P stack universally have
quadratic query traffic, nor that this prototype replaces those protocols.

## 5. Reproduce, verify, and use the result responsibly

Python 3.12+; existing standard-library runtime, no new data download:

```sh
python3 scripts/benchmark_swarm_topology.py
python3 scripts/benchmark_swarm_topology.py --out .cache/swarm/replay.json
python3 -m unittest discover -s tests -p 'test_swarm_topology.py' -v
python3 -m unittest discover -s tests -v
forge test --offline -v
```

The first command checks exact replay of the committed report. The separate
output can be compared byte-for-byte; sources and inputs are SHA-256 bound.
The initial full-source conversion is not rerun here. The bundled aggregation
makes the overlay and simulation reproducible independently of private modules.
The source record and all original observations remain available inside the report.

Local validation: **83 Python tests passed on Python 3.12.13 and 3.14.5**, including
13 new topology/policy/evidence tests. The complete report reproduced byte-for-byte
across those Python versions. `forge test --offline -v` passed **10 unchanged
Solidity tests**, including 128 fuzz runs. These are local regression results,
not an independent scientific review or a hardware benchmark. Verification hashes
are recorded in the protocol lock separately from its pre-run source snapshot.

A defensible statement is: **a MaleCNS-informed sparse overlay retained four
origin events in the healthy hot-key model, substantially reduced assumed peer
traffic relative to all-peer querying, and outperformed the original ring and
geographic alternatives in one specified failure sample.** The matched and
multi-seed controls limit broader anatomical claims.

No physical NVMe reduction, lower monthly bill, biological self-healing,
permissionless deployment or universally optimal routing is established. Real BSC
capacity and cost claims require authorized node profiling with an equal-service
latency/correctness contract and accounting for relay CPU, memory, discovery,
timeouts, trust and recovery. This simulation is a candidate-screening result,
not that deployment evidence.
