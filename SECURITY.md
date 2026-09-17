# Security and trust boundaries

Research software, not an audited production protocol. Do not deposit funds.
There is no token, payout, buyback or key-management functionality.

- Keeper APIs bind to loopback by default. Remote operation requires deliberate
  operator deployment behind TLS, access controls and external rate limits.
- Admin bearer tokens belong in local environment variables, never in Git,
  command examples, screenshots or bug reports.
- State-continuity keeper peers are explicit, bounded and independently replay-verified. They can withhold
  data, delay responses or propose different valid input histories. Forks fail
  closed instead of silently changing the selected history.
- Full data availability is an assumption: hashes and on-chain events cannot
  reconstruct missing checkpoints or graphs. Preserve multiple copies.
- The immutable registry committee is permissioned. A 2-of-3 committee tolerates
  one unavailable witness, not two colluding witnesses. Witnesses can falsely
  attest scientific correctness; the contract checks signatures, not biology.
- Chain ID alone does not identify the real public BSC network. Local Anvil tests
  are labeled local. Public receipt checks trust the configured RPC and do not
  implement a PoSA light client or finality proof.
- Python source/model upgrades can change semantics. Fork a new model/run version
  rather than silently changing the meaning of already committed state.
- Loopback demonstrations share a host and operator. Independent deployment,
  Internet-scale DoS tests, Byzantine consensus, witness rotation and archival economics
  remain unimplemented.

## RPC edge boundaries (v0.2)

- Only four read methods are supported. There is no `eth_call`, transaction
  submission, key management, tracing or user-selected upstream URL.
- The daemon binds only to loopback and rejects browser Origin headers and
  non-loopback Host values. Do not expose it through a public tunnel or reverse
  proxy and assume that it has authentication, quotas or Internet abuse protection.
- Chain ID plus genesis pins a configured network namespace at startup. It does
  not prove RPC honesty or consensus. No trie-proof or light-client verification
  is implemented; a dishonest upstream can return plausible but incorrect values.
- Only explicit noncanonical-required block-hash reads may be cached/coalesced.
  Canonical-required and dynamic selectors always go upstream. Cached historical
  bytes may outlive origin pruning until expiry; origin error availability is not
  preserved in that case. No automatic chain-head tracking is claimed.
- Cache entries/bytes, origin concurrency, handlers, bodies and JSON depth are
  bounded. Socket deadlines interrupt trickling upstream headers/bodies after
  connect; platform DNS resolution is not an absolute-deadline guarantee.
- Redirects are rejected, TLS verification stays enabled and unsupported configured
  remote proxy routes fail explicitly. Aggregate metrics omit request addresses,
  bodies and keys. Partial failed transport bytes are not a complete wire-cost metric.
- The graph lookup benchmark is a separate simulation over trusted logical cache
  replicas, not an authenticated or Byzantine-tolerant peer-cache protocol.

Before external publication, review the exact files and remove all runtime stores,
credentials and local logs. Follow the release checklist; do not treat CI green as
a security audit or a claim of biological/financial correctness.

## Experimental Synaptic Edge Daemon / Mesh (PR #8)

The older v0.2 server above is unchanged. `scripts/run_edge_daemon.py` is a separate
six-method profile with opt-in `eth_call`, bounded async ingress, exact CORS,
remote-bind token requirements and an optional block sampler. It has **not** had
an external security audit, public hostile-WAN trial or long-duration soak test.

Mesh peers are NOT replay-verified keeper peers. They share an operator-managed
bearer credential and return pinned cache values; well-formed dishonest data
cannot be detected without state proofs. Do not accept arbitrary public peers or
call this permissionless consensus. RPC authentication does not replace TLS,
firewalls, per-client quotas or upstream execution-gas caps. Default loopback
binding protects against accidental remote exposure, not malicious local users.

Predictions never answer `eth_call`; reviewed catalogs are advisory and
network/code-hash scoped. Prefetch can add origin traffic and compete for cache
space or in-flight slots. It is bounded and can be disabled. No daemon method
retrains from untrusted transactions, traces code, accepts a dynamic upstream,
or grants wallet permissions.

Offload receipts are unsigned operator accounting. Hash chains expose alteration
relative to a retained trusted head, not fabrication, independent execution,
availability or economic entitlement. They are not registry witness certificates.
Runtime reports may reveal query targets and should remain private unless reviewed.

See [deployment and resource boundaries](docs/edge-daemon-design.md).
