# Tier-1 offloading: weighted reassessment and 1,000-client load

**Research candidate, 2026-09-16. Not a deployed network or a v0.3 release tag.**

The engineering objective is to reduce expensive upstream state service while
returning correct answers. Ordinary hash maps, LRU and single-flight remain the
implementation primitives. Tiny peer-byte differences must not be mistaken for
a complete system-cost comparison.

There are two independent results: a scenario reweighting of the saved cooperative
simulation, and a new actual TCP/HTTP test against an owned local EVM origin.
Neither experiment measures physical NVMe wear, GPU use or a hardware invoice.

## 1. Explicit tier weights, unchanged observations

```text
C_scenario = W_origin × origin_state_reads + peer_JSON_bytes / 1,000,000
W_origin = 200, with sensitivity at 100 and 500
W_peer   = 1 per decimal MB of peer JSON
```

These are declared relative weights, not USD prices or an empirically established
minimum cost ratio. The dimensions matter: at the default, one state read is
assigned the same score as **200 MB** of peer JSON, not 200 individual bytes.
CPU, memory, verification, operations and fixed-capacity realization are outside
this two-component score. All underlying counters remain available separately.

For the historical cooperative counter snapshot, each healthy balanced-placement
aggregate serves the same 2,048 requests. These are **discrete-simulation counters**,
not the actual HTTP measurements in Section 2:

| Arm | Origin reads | Peer JSON MB | Score at 200/1 |
|---|---:|---:|---:|
| A: geographic, independent | 1,314 | 3.502261 | 262,803.502261 |
| B: affinity, independent | 1,314 | 4.284406 | 262,804.284406 |
| C: geographic, cooperative | 1,196 | 3.410981 | 239,203.410981 |
| D: affinity, cooperative | 1,196 | 4.151932 | 239,204.151932 |

**A → D:** 118 fewer origin reads, an **8.980213% read reduction**. The extra
0.649671 MB costs only 0.649671 scenario units against 23,600 units of avoided
origin work. Net score reduction: **23,599.350329 units / 8.979846%**.

| Origin weight per read | A → D score reduction |
|---:|---:|
| 100 | 8.979479% |
| 200 | 8.979846% |
| 500 | 8.980066% |

Across **all 640 exported rows / 320 cooperation-on/off pairs**, including failures,
240 pairs improve, 80 tie and none worsen under each weight. No counter cases
were dropped.
This supports coordination as an upstream-offloading direction under the declared
scenario; it is not a fleet-dollar estimate.

**D versus C:** both issue 1,196 origin reads. D's extra 0.740951 MB adds just
0.000310% to C's default score. The anatomical overlay still has no additional
demonstrated read reduction, but this tiny premium does not erase the system's
cooperation benefit. Biology-specific attribution and system usefulness are
different questions.

Existing smaller results also remain correctly labeled:

| Evidence | Before → after | Reduction | Scope |
|---|---:|---:|---|
| v0.2 HTTP, all origin RPCs | 40 → 18 | 55% | Includes two identity checks per arm |
| Same HTTP test, state reads only | 38 → 16 | 57.894737% | Synthetic local workload |
| Saved public BSC probe, state reads | 16 → 4 | 75% | Controlled fixed-block compatibility probe |

The public probe made 27 total public RPC calls, including setup, and sent zero
transactions. It was **not rerun** for this reassessment. These two earlier tests
did not measure inter-peer traffic; their reweighted values cover only the origin
component. The 55%/75% figures do not describe the 1,314 → 1,196 experiment.

The complete counter export is embedded in the scenario report, pinned by a
canonical SHA-256 digest and checked for all layout/workload/failure/control
combinations. The original report digest is retained as provenance. **The original
simulator and exploratory models are not included in this release.** Reproduction
here verifies input integrity and reweighting arithmetic, not independent execution
of that historical simulation. The new HTTP benchmark below is fully runnable
from this release without those files.

[Standalone reweighting code](../scripts/reevaluate_tier_cost.py) ·
[Complete counter snapshot, scenario output and hashes](../results/tiered-cost-reevaluation.json)

## 2. Real 1,000-client HTTP bursts

The [driver](../scripts/benchmark_tier_offload.py) opens 1,000 actual local HTTP
clients. All request bodies reach the server before a synchronized dispatch gate
opens. Each normal case records **1,000 admitted requests**, with a **16-worker
dispatch limit**. This is concurrent admission, not 1,000 simultaneous origin
workers. Four logical groups receive 250 requests each.

The new [experimental asyncio ingress](../synafly_lab/async_edge_ingress.py) is
loopback-only, bounded and separate from the existing v0.2 daemon. It calls the
unchanged read policy/cache/coalescing implementation. No live service was changed.

Compare fresh caches and the same worker limit:

- **Pass-through:** four uncached, noncoalescing edges.
- **Isolated:** four ordinary independent LRU/single-flight edges.
- **Shared:** those four edges use one local HTTP coordinator for cacheable keys.
  Mutable selectors go directly to origin, without caching or coalescing.

An owned Anvil fixture supplies 1,000 distinct synthetic balances and a pinned
block. Responses are actual EVM JSON-RPC results, checked against the exact
expected value and request ID. A declared **20 ms emulated delay per origin read**
exercises overlap; it is not a measured BSC/NVMe service time. No public endpoint
was load-tested, no public transaction was sent and no paid infrastructure was
provisioned.

### Origin reads and correctness

| 1,000-query workload | Pass-through | Isolated caches | Shared coordinator | Shared reduction vs pass-through |
|---|---:|---:|---:|---:|
| One repeated pinned key | 1,000 | 4 | 1 | 99.9% |
| Eight repeated pinned keys | 1,000 | 32 | 8 | 99.2% |
| 1,000 distinct pinned keys | 1,000 | 1,000 | 1,000 | 0% |
| Repeated `latest` selector | 1,000 | 1,000 | 1,000 | 0% |

**12,000 of 12,000 normal responses were correct**, with zero HTTP rejections,
transport errors or incorrect/RPC-error responses. Source identity checks are
separate: eight per pass-through/isolated case and two per shared case. They are
not mixed into state-read reduction.

Ordinary local caching already removes most repeated reads. Shared coordination
then reduces the remaining hot-key origin work by **75%** relative to the isolated
baseline: 4 → 1 and 32 → 8. The coordinator recorded 3 and 24 coalesced waiters,
respectively, with zero coordinator cache hits in those cases. Cross-group
overlapping misses were actually merged; the result is not just a fabricated hit
counter or a comparison against no cache.

These logical groups are **not measured neuropils**. The coordinator is on one
host, under one operator; no independent-peer/WAN decentralization is established.
The demonstrated mechanism is conventional shared single-flight coordination,
not a proven MaleCNS-specific optimization.

### Peer traffic and weighted result

| Shared workload | Measured peer JSON bytes | Score at 200/1 | Pass-through score |
|---|---:|---:|---:|
| One pinned key | 1,020 | 200.001020 | 200,000 |
| Eight pinned keys | 8,160 | 1,600.008160 | 200,000 |
| Distinct pinned keys | 258,136 | 200,000.258136 | 200,000 |
| `latest` | 0 | 200,000 | 200,000 |

Both peer request and response bodies are counted from actual HTTP exchanges.
These byte totals exclude TCP/TLS headers and client-edge/edge-origin traffic.
The no-reuse case has a small positive peer overhead and **no origin savings**.
Mutable selectors bypass the coordinator directly, removing unnecessary peer
traffic but not upstream reads. The standalone capture was rerun after removing
the old training-module dependency; its
[pre-run source lock](../results/tier-offload-protocol-lock.json) matches the
implementation in this release. Earlier private working runs are not inputs to
this capture.

### Latency is a separate acceptance criterion

Captured local p95 latency, milliseconds:

| Workload | Pass-through | Isolated | Shared |
|---|---:|---:|---:|
| One pinned key | 1,577.536 | 168.347 | 472.800 |
| Eight pinned keys | 1,538.783 | 197.173 | 535.927 |
| Distinct pinned keys | 1,895.736 | 1,556.249 | 2,025.081 |
| `latest` | 1,546.060 | 1,908.602 | 1,586.411 |

These are single captured local bursts, not statistical performance estimates.
They include the synchronization gate and synthetic delay, and run sequentially
on a shared host. Both shared hot-key cases have worse p95 latency than isolated
despite fewer origin reads; the extra coordination is not free. No uniform latency
improvement, WAN SLO or production throughput guarantee follows. Raw p50/p95/p99
values are all retained.

### Overload is not savings

A separate 1,000-client burst with capacity 64 returned **64 correct responses
and 936 explicit HTTP 503 rejections**. There were no transport errors. The report
sets its headline offload percentage and weighted score to `null`, and marks it
ineligible for equal-service comparison. Rejected requests must never inflate
the offload claim.

[Final raw load evidence](../results/tier-offload-1000.json) ·
[Evidence verifier](../scripts/verify_tier_offload.py) ·
[Design and boundaries](tiered-load-test-design.md)

## 3. What this changes about the research direction

The next objective is **correct upstream offloading at an acceptable service
level**, not replacing hash tables with neurons. Event-triggered communication,
partition-level reuse and continuity are useful architectural research themes.
They are not interchangeable with a light client, Verkle witness verification or
a full brain emulation. Existing Keeper recovery evidence remains separate; this
load test does not establish coordinator crash recovery.

For BSC relevance, full nodes retain state and execute/validate blocks; validators
have consensus obligations beyond optional read traffic. Public RPC service must
not be assumed to run directly on every validator. See the official
[full-node responsibilities](https://docs.bnbchain.org/bnb-smart-chain/developers/node_operators/full_node/)
and [validator guidance](https://docs.bnbchain.org/bnb-smart-chain/validator/overview/).

The immediate positive claim is **fewer upstream read executions for stated
workloads**. Converting that to hardware expense needs an operator-owned BSC
measurement: CPU, memory, block-import impact, physical I/O, correctness and p99
at equal offered load, followed by demonstrated capacity reduction. Ordinary RPC
reads do not incur on-chain gas fees; this test measures neither GPUs nor NVMe
wear. A fixed monthly server bill does not automatically shrink with query count.

The existing [financial scenario calculator](../scripts/cost_scenario.py) requires
the avoidable RPC share, offload share and realized capacity-reduction share,
then subtracts relay/verification/operations expense. A hypothetical monthly
price alone is insufficient. No dollar amount is promoted to a measured result.

## Reproduce and audit

The verification record is generated against this clean release, not the larger
private research workspace. It covers the complete Python suite, all 12 normal
load bursts, overload exclusion and arithmetic over the 640-row counter snapshot.
The complete suite passed **70 tests on Python 3.12.13 and 3.14.5**; the unchanged
Solidity suite passed 10 tests, including 128 fuzz runs. Local test results are
distinct from GitHub CI and production verification.
[Verification record](../results/tier-offload-verification.json).

Python 3.12+ and installed Anvil are needed to rerun the local load. No public RPC,
wallet, private key or new dataset is required. Use a separate output path to
preserve the captured report:

```sh
python3 scripts/reevaluate_tier_cost.py
python3 scripts/reevaluate_tier_cost.py --out .cache/tier-offload/cost-replay.json
python3 scripts/benchmark_tier_offload.py --clients 1000 --out .cache/tier-offload/replay.json
python3 scripts/verify_tier_offload.py --report .cache/tier-offload/replay.json
python3 scripts/verify_tier_offload.py
python3 -m unittest discover -s tests -p 'test_tier_offload.py' -v
```

Scenario arithmetic is deterministic. Runtime latency is not; coalescing counts
also depend on actual overlap and should be inspected if a rerun differs. The
verifier binds current implementation sources to the saved report and checks
coverage, concurrency, correctness, read/peer accounting and overload exclusion.
It is a reproducibility check, not an independent audit or proof of field savings.
