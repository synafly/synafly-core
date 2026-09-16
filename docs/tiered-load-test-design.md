# Tier-1 offloading and a 1,000-client HTTP burst

The objective is upstream work avoided with correct completed requests. Keep
ordinary hash maps, bounded LRU and single-flight; do not treat small peer-byte
differences as a complete system-cost comparison.

## Declared cost scenario

Use 200 relative units per origin state read and 1 per decimal MB of peer JSON,
with origin-weight sensitivity at 100 and 500. This is an assumed exchange rate,
not an observed lower bound, USD price or hardware invoice model. One read is
assigned the same score as 200 MB of peer JSON at the default. Retain physical
counters separately; no read-count-to-GPU/NVMe conversion is justified.

## Transport and capacity

The unchanged `ReadEdge` parser, bounded LRU and single-flight implementation
provide state semantics. A separate **experimental loopback asyncio ingress**
allows up to 1,024 admitted requests, 16 dispatch workers, bounded headers/bodies,
no browser origins and explicit 503 overload responses. The existing v0.2 daemon
is neither modified nor claimed to support 1,000 simultaneous clients.

The driver opens 1,000 real local HTTP clients. Dispatch waits until all request
bodies have arrived, then releases the synchronized burst. Record actual
admission/connection high-water marks. This is **1,000 concurrent arrivals**, not
1,000 parallel origin workers. Origin work remains bounded by 16 front workers.

Four logical groups each receive 250 requests. Compare fresh caches:

- **Pass-through:** four uncached, noncoalescing edges with the same worker limit.
- **Isolated:** four independent ordinary cache/coalescing instances.
- **Shared:** the four instances use a common local HTTP coordinator with the
  same cache/coalescing primitive. Measure every peer request/response JSON body.

Uncacheable selectors bypass the coordinator and go directly to the configured
origin, without caching/coalescing. Logical groups and a centralized coordinator
on one host are not measured neuropils, permissionless peers or a WAN deployment.

## Workload and negative controls

Per mode, offer 1,000 queries against fresh empty caches:

1. One identical pinned-state key.
2. Eight pinned keys shared across all four groups.
3. One thousand distinct pinned keys.
4. One identical `latest` query, which must bypass cache/coalescing.

The standalone driver starts an owned zero-account, no-mining Anvil fixture.
Set 1,000 distinct synthetic balances and mine/capture one fixed block. Check
actual JSON-RPC values and caller IDs against the fixture. The minimal fixture
is inside the driver; no training/brain-simulation module is required.

Add a declared 20 ms delay per origin read to exercise overlap. This is emulated
latency, not measured BSC/NVMe latency. Never load-test a public endpoint. Record
fixture setup, readiness and per-origin identity calls separately from state reads.

Also offer 1,000 requests with admission capacity 64. Record successful replies
and rejections separately. Reject any attempt to count overload as saved work.

## Report and reproducibility

- Offered, admitted, completed, rejected and errored requests; active worker peaks.
- Exact expected values and caller IDs for every successful response.
- Origin state reads, metadata calls, cache hits, coalesced waiters and bypasses.
- Peer JSON bodies separate from client-edge and edge-origin bytes; no assumption
  that JSON body bytes are complete TCP/TLS transport costs.
- p50/p95/p99 client latency, including the deliberate synchronization gate;
  no WAN SLO or production throughput claim from a single local capture.
- Weighted scores only for equal workloads completed entirely correctly.
- Source hashes fixed before the capture and checked against the resulting report.

The separate cost reevaluation carries the **complete 640-row counter export**
from an earlier discrete cooperative simulation, including failures and controls.
The simulator and exploratory models are not dependencies or part of this release.
Replay verifies snapshot integrity, case coverage and arithmetic, not the original
simulation execution. Older v0.2 HTTP/public-probe inputs already exist on `main`;
the public probe is not rerun automatically.

An observed source-count reduction does not itself establish a smaller server
bill. Fixed costs fall only if capacity can be reduced without violating service
and security obligations. RPC reads do not incur on-chain gas fees. No measured
GPU saving, physical NVMe wear reduction or biological superiority is claimed.
