# Edge daemon & mesh: recorded results

Base: `b5cb0cd` (PR #7). Scope: owned localhost wire fixtures and independent
daemon processes. No public BSC RPC, transaction, public mesh deployment or registry
commit was performed for this release. No financial savings are inferred.

## Cold burst: real HTTP, fixed worker pool

The client releases all tasks together. The origin has 16 workers and an emulated
10 ms state-read service time. The edge also has 16 foreground workers; admission
capacity is explicitly shown. This is not 5,000 simultaneous EVM executions.
Each row is one measured trial on a shared development host, not a hardware-neutral
latency promise. A 65-second inter-case cooldown separates bursts.

| Workload | Clients | Edge capacity | Correct edge responses | Direct origin reads | Edge origin reads | Rejections |
|---|---:|---:|---:|---:|---:|---:|
| hot | 1,000 | 1,024 | 1,000 | 1,000 | 1 | 0 |
| unique | 1,000 | 1,024 | 1,000 | 1,000 | 1,000 | 0 |
| latest | 1,000 | 1,024 | 1,000 | 1,000 | 1,000 | 0 |
| hot | 5,000 | 5,000 | 5,000 | 5,000 | 1 | 0 |
| unique | 5,000 | 5,000 | 5,000 | 5,000 | 5,000 | 0 |
| latest | 5,000 | 5,000 | 5,000 | 5,000 | 5,000 | 0 |
| hot | 5,000 | 64 | 64 | 5,000 | 1 | 4,936 |

The equal-admission hot cases reduce origin reads by **99.9% (1,000 → 1)** and
**99.98% (5,000 → 1)**. This is ordinary LRU/single-flight reuse of one explicitly
pinned key. It does not establish a FlyHash or biological topology advantage.
Unique keys and `latest` calls return all requested values and save zero reads.
The capacity-64 row is a rejection test: **4,936 rejected requests are excluded**
from offload, and its 64 successes cannot be compared as 5,000 fulfilled reads.

Across the six full-admission rows: 18,000 edge responses and 18,000 direct-origin
responses matched the deterministic fixture. No wrong values or transport errors
were observed in this cooled sweep. The overload row has 5,000 correct direct
responses, 64 correct edge responses and 4,936 explicit 503s.

### End-to-end successful response latency

| Workload / clients / capacity | Direct P50/P90/P99 (ms) | Edge P50/P90/P99 (ms) | Edge peak RSS (MiB) | Edge CPU user+system (s) |
|---|---|---|---:|---:|
| hot / 1000 / 1024 | 507.558 / 808.991 / 877.674 | 76.073 / 91.081 / 113.931 | 41.77 | 0.1848 |
| unique / 1000 / 1024 | 475.133 / 775.733 / 845.45 | 517.153 / 824.908 / 902.837 | 43.27 | 0.6231 |
| latest / 1000 / 1024 | 504.668 / 810.762 / 884.434 | 524.431 / 882.082 / 965.061 | 42.83 | 0.6241 |
| hot / 5000 / 5000 | 2357.26 / 3859.766 / 4195.394 | 367.541 / 456.296 / 477.979 | 81.27 | 0.6350 |
| unique / 5000 / 5000 | 2428.732 / 3934.287 / 4272.303 | 2533.404 / 4317.395 / 4666.959 | 83.27 | 3.0345 |
| latest / 5000 / 5000 | 2391.288 / 3962.734 / 4303.856 | 2583.07 / 4447.693 / 4854.784 | 81.53 | 3.0602 |
| hot / 5000 / 64 | 2476.169 / 4068.323 / 4431.911 | 292.032 / 292.576 / 292.816 | 35.77 | 0.1793 |

Percentiles include connection/admission/queue time, for **successful responses
only**. CPU and RSS are daemon-process lifetime measurements including startup;
the load generator and origin are separate from the daemon RSS. `latest`/unique
traffic pays proxy overhead rather than gaining a cache benefit. These are not
physical NVMe IOPS or paid-provider billing measurements.

### Initial no-cooldown sweep retained

[The initial report](../results/edge-daemon-stress-no-cooldown.json) records severe
transport/RPC failures in later 5,000-client unique/latest cases. No reduced origin
count in those failed cases is reported as savings. The precise cause was not
instrumented; short-connection/host resource pressure is a hypothesis, not a proven
diagnosis. A cooled sweep succeeded, but this does **not** demonstrate sustained
high-rate service or resolve every possible cause. Connection reuse, long-duration
soak testing, kernel/socket telemetry and multi-host trials remain follow-up work.

## Independent processes and predictive cache use

[The process report](../results/edge-daemon-verification.json) starts three real
daemon processes over the test, two of them simultaneously in the mesh:

1. Warm a pinned slot on a configured PR #5 neighbor role.
2. Read it through a separate node: real peer HTTP returns the matching value;
   the requester makes no extra origin storage call.
3. Stop the neighbor process, request a new slot: peer failure is counted and
   the requester obtains the result from its origin.
4. A third daemon samples a synthetic block with selector `0x0902f1ac`, matches
   its reviewed synthetic runtime bytecode, queries FlyHash and fills slot 8
   **before any client storage request**. That subsequent read hits cache.

The fixture code is not PancakeSwap. Slot 8 and the selector are used to exercise
the mechanism, not to certify protocol-wide prediction accuracy. Public-chain
catalog coverage, distributed operators and adversarial WAN operation are untested.

A paired unit test also demonstrates the cost trade-off: one `eth_call` plus
one later storage read costs three origin calls with cold prefetch (call + code +
slot), compared with two ordinary forwards. Foreground avoidance is 50%, but net
RPC reduction is **−50%**. Additional prefetch traffic is never hidden.

## Regression, algorithm and receipt checks

- 128 Python tests pass (102 prior + 26 new) on CPython 3.12 and 3.14.
- The existing 10 Foundry tests pass offline, including 128 fuzz runs.
- The runtime index reproduces all **1,280 frozen PR #7 predictions** across
  all five methods, with the identical catalog fingerprint and no cast subprocess.
- Ethereum Keccak matches 305 prior wire vectors plus six independently generated
  cast vectors at rate boundaries and 4 KiB. SHA3-256 is explicitly not substituted.
- 10,000 local reuses retain one cache entry, no in-flight keys and only the
  configured eight receipt entries. This bounds those containers, not all future
  process behavior or a multi-day memory leak claim.
- Receipt alteration breaks the SHA-256 chain; exported fields encode through
  the existing registry ABI adapter after normalizing bytes32 prefixes. No
  witness certificate or on-chain receipt was generated.
- Strict method/Host/Origin/auth rules, invalid catalogs, unknown code, malformed
  peers, canonical-required bypass, changing blocks, prefetch failure and SIGTERM
  shutdown/report export are exercised through offline tests.
- Original RPC/cache/recipe sources and PR #4–#7 reports remain unchanged;
  historical reproduction and source-binding checks pass.

## Reproduction and remaining work

```sh
python3 scripts/verify_edge_daemon.py
python3 scripts/stress_edge_daemon.py --clients 1000 5000
python3 scripts/verify_daemon_evidence.py
```

The last command checks the published file hashes and accounting; it does not
rerun the experiment. CI runs the Python suite, historical checks, offline
three-process test and release-lock verification. It does not contact BSC.

**Not verified here:** Docker image build/run (no Docker engine installed),
public mainnet runtime catalog, mempool ingestion, independent peer state proofs,
peer auto-discovery, full MetaMask support, WAN performance, continuous 5,000-client
load, production hardening/audit, actual node cost reduction or economic incentives.

See [design, CLI, trust model and deployment instructions](edge-daemon-design.md).
