# Research release v0.2 — Read edges and topology controls

This release adds executable RPC infrastructure and controlled comparative
experiments to the state-continuity core. It makes no claim that a private,
completed node-replacement system exists or that BSC savings have been established.

## Delivered mechanisms

The loopback RPC daemon validates four read methods, pins network identity,
preserves caller IDs, caches eligible immutable block-hash reads, coalesces
simultaneous misses and bounds resource use. Dynamic/canonical-required reads go
upstream. Errors do not become cache entries; in-flight failures wake followers
and release capacity. The [API profile](rpc-edge.md) documents the trust and
availability assumptions, including what this implementation intentionally bypasses.

The biological graph is **not integrated into the HTTP data path**. The ordinary
edge and the graph experiment are separate so a cache benefit cannot be mislabeled
as a biological routing advantage.

## Experiment A: actual HTTP and separate edge processes

Run `python3 scripts/demo_edge.py`. Every configuration uses the same 38-state-query
fixture: cold concurrent requests, warm repeats, a different block, changing
`latest` state, canonicality failures and missing blocks. The fixture controls
when reads complete so simultaneous misses are observed rather than assumed.
The two fixture block hashes compete at one synthetic height; changing the head
orphans the other. Local health/metrics GETs are not state queries and never call
the origin. This fixture is not a full blockchain emulator.

| Configuration | State reads at origin | Identity calls | Total origin RPC calls |
|---|---:|---:|---:|
| Pass-through | 38 | 2 | 40 |
| Cache only | 23 | 2 | 25 |
| Coalescing only | 31 | 2 | 33 |
| Both | 16 | 2 | 18 |

All expected outcomes match. [Full counters and fingerprints](../results/edge-http.json).
The repeat distribution is synthetic and deliberately stated. These are not BSC
traffic statistics, measured CPU savings, WAN latency or a financial estimate.
Reported JSON byte counts exclude HTTP/TLS overhead and do not estimate host RAM.

## Experiment B: public BSC read compatibility

The opt-in probe selects a block after identity bootstrap, then performs four
rounds of four read methods with paired baseline/cache requests. It uses one
neutral public address, no credentials and no transactions. Four paired workers
reduce query-time skew; this is not a latency or throughput benchmark.

The captured run matched normalized values for all 16 logical reads. Pass-through
made 16 state reads; the cache made 4. Each configuration also made two identity
calls; shared capture made three calls, for **27 actual public RPC calls total**.
The [report](../results/bsc-read-probe.json) records block identity, values, counters,
provider and implementation hashes.

Provider limitations matter. Public read attempts encountered a transport
interruption and a later upstream RPC rejection during a sequential comparison.
The RPC rejection's root cause was not established; it must not be relabeled as a
proven reorg or pruning event. The harness now records failed attempts rather than
leaving an earlier success looking current and pairs observations to reduce time
skew. [Failure observations](../results/bsc-probe-observations.json). A successful
sample is not an availability guarantee; providers can reject or prune state.

## Experiment C: topology is not automatically an advantage

Run `python3 scripts/benchmark_routing.py`. It keeps the full 256-node/605-edge
excerpt, without selecting only its well-connected part. Directed reachability is
**15,775 / 65,280 ordered pairs**; 27 nodes have no outgoing edge in this excerpt.
That is a property of this biased sample, not a claim about the complete brain.

The experiment compares the observed adjacency, eight degree-matched rewirings,
a conventional equal-edge-budget overlay and a direct-owner reference. Every
strategy sees the same graph-independent ownership, warm replicas, entry points,
five workload seeds and 0/5/20-percent synthetic node-failure scenarios. Edge-based
lookup is limited to 32 contacts and 8 hops. Failed contacts and origin fallbacks
are counted; synaptic weights are not used in the lookup policy.

Zero-failure results, aggregated over five seeds:

| Strategy | Shard hits / 2,560 | Lookup contact attempts |
|---|---:|---:|
| Observed MaleCNS excerpt | 668 | 50,729 |
| Eight degree-matched controls | 723–822 | 53,792–57,740 |
| Conventional equal-edge-budget overlay | 832 | 67,611 |
| Direct-owner reference | 2,560 | 2,531 |

The direct-owner reference can address a known owner without a graph constraint;
it is **not** a matched edge-budget control. Directory discovery and maintenance
are not modeled. Its purpose is to expose the practical alternative to imposing
an unnecessary routing graph when owner locations are already known.

**No biological advantage was established.** The observed graph uses fewer
contacts but misses more cache shards than the controls. An economic ranking would
need measured origin/peer costs and real workloads; the report does not invent one.
Uniform synthetic demand, warm caches, incomplete connectivity and abstract
contact counts limit generalization. [Every trial and seed](../results/routing-benchmark.json).

## Next falsifiable questions

1. Does a separately measured workload provide correlations that justify a
   biological prior rather than an arbitrary graph-to-query mapping?
2. Can a role-specific overlay preserve correctness while outperforming both
   matched graph controls and conventional owner lookup with equal resource accounting?
3. Can independent operators reproduce useful results over real networks, including
   churn, malicious peers, discovery cost and complete retained-state accounting?

These are subsequent research gates. Public registry deployment, independent
keeper operation, biological RPC routing and full-node equivalence remain open.

## Reproduction

```sh
python3 -m unittest discover -s tests -v
python3 scripts/demo_edge.py --out .cache/repro/edge-http.json
python3 scripts/benchmark_routing.py --out .cache/repro/routing-benchmark.json
python3 scripts/verify_evidence.py --edge .cache/repro/edge-http.json --routing .cache/repro/routing-benchmark.json
```

CI reproduces the offline experiments. The optional public probe is separate:

```sh
python3 scripts/probe_bsc_rpc.py --upstream https://bsc-dataseed.bnbchain.org --out .cache/repro/bsc-read-probe.json
```

A public rerun can select another block, return different values or fail. This
release publishes the observed evidence and its boundaries, not a guarantee about
future provider behavior. Source disclosure proceeds module by module, with
reproducible methods and explicit acceptance criteria for subsequent work.
