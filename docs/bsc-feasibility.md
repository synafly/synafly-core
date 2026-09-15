# Bio-Sparse Synaptic Relay (BSSR)

**Feasibility blueprint for an outer BSC edge layer.**

BSSR is a research and engineering program, not an already deployed RPC network.
The objective is ambitious: investigate whether sparse, connectome-inspired
coordination can reduce avoidable edge work while retaining exact response
semantics and an explicit security model. The present release establishes
state-continuity primitives; it does not establish those performance advantages.

## Problem and hypothesis

Repeated read requests, redundant transfer and uneven load are plausible sources
of edge overhead. Their prevalence must be measured for a specified workload.
This repository has no ecosystem-wide dataset establishing approximately 2,000
nodes, 85–92% repeated traffic, or $1,000–1,500 monthly cost per node. Such values
may be explored as labeled assumptions, not described as observed telemetry.

Biological connectomes motivate sparse graph experiments. They do not prove that
software emulation inherits a fly's physical energy efficiency, nor that a
biological topology is optimal for RPC traffic. This release makes no universal
claim about lifespan, micro/milliwatt consumption or a fixed percentage of active
synapses. The model parameters are illustrative and the connection sample is small.

## Architecture and role separation

![BSSR proposal and evidence gates](diagrams/bssr-gates.svg)

| Role | Proposed intervention | Required proof | Current status |
|---|---|---|---|
| Public RPC edge | Exact-context coalescing and bounded read cache | Correct cache keys, freshness/finality policy, reorg handling and measured origin-call reduction | Not implemented here; the live website is not an RPC cache |
| Data distribution | Sparse directed peer dissemination | Transfer cost, tail latency, failures and churn against matched baselines | Keeper checkpoint pulls tested locally; general routing not implemented |
| State continuity | Replay-verified checkpoint hash chains | Restored graph/input/state matches uninterrupted execution | Implemented; single-host multi-process experiment |
| Commitment ordering | Fixed-quorum EVM registry | Signature, parent, sequence and model-binding correctness; then public-chain evidence | `ContinuityRegistry.sol` tested locally; public BSC deployment pending |
| Consensus/full-node execution | Leave PoSA and EVM transitions unchanged | Protocol-equivalent outputs for any claimed replacement role | Not implemented or replaced |

The proposed edge layer does not change consensus rules. That is **not zero
security risk**: stale caches, untrusted peers, availability failures and incorrect
proof verification still matter. A connectome can contain cycles; an acyclic
routing overlay would be an explicit derived design, not an assumed property of
all biological connections.

## Cost scenarios, not measured savings

For a defined fleet and accounting boundary, use:

    baseline_annual = 12 × N × monthly_node_cost
    gross_avoidable = baseline_annual × rpc_cost_share × offload_share × realization_share
    net_annual = gross_avoidable − relay_cost − extra_verification_cost − operations_cost

All added costs above are annual. The factors have different meanings:

- `N`: applicable nodes, not an assumed count of every validator/full/archive node.
- `rpc_cost_share`: the portion of the bill attributable to avoidable edge work.
- `offload_share`: the portion of that workload successfully removed upstream.
- `realization_share`: how much released capacity becomes actual lower spending.
- Added costs include replacement infrastructure and its verification/maintenance.

A reduction in request count is not an equal reduction in total node cost. Fixed
capacity may remain provisioned, or work may simply move into clients and relays.

The earlier expression `2000 × monthly_cost × alpha × 12` implicitly treated the
whole node bill as avoidable RPC cost and omitted replacement costs. Even under
that optimistic formula, the ranges $1,000–1,500 and alpha 0.85–0.92 produce
$20.4M–$33.12M gross—not a derived $20.24M point estimate. These are hypothetical
arithmetic outputs, **not validated BSC economic facts**.

The executable [scenario calculator](../scripts/cost_scenario.py) and
[illustrative inputs](../data/cost-scenario-example.json) make the assumptions
inspectable. Its tests validate arithmetic, not the realism of the inputs.

## Four evidence gates

### Gate 1 — deterministic continuity: passed locally

Recover graph and input/checkpoint history from a surviving keeper after the
original process terminates. Verify bitwise parity with uninterrupted execution.
Evidence: `scripts/demo_continuity.py`, `scripts/verify_history.py` and
`results/continuity.json`. This does not establish geographical fault tolerance
or an unattended self-healing control plane.

### Gate 2 — quorum commitment mechanism: passed in local EVM

`contracts/ContinuityRegistry.sol` uses **EIP-191 personal-sign**, with the chain ID
and registry address bound into the message. It enforces an immutable witness
quorum, sequence and parent continuity. Foundry and actual local Anvil receipts
exercise the mechanism. Public BSC anchoring and independent witness operation
remain separate, incomplete milestones.

### Gate 3 — role-correct BSSR prototype: pending

Implement an explicit read-only RPC surface, cache/coalescing semantics, peer
selection and fallback. Test stale state, reorgs, duplicate requests, malicious
responses and bounded resource behavior before reporting an offload percentage.
Compare against conventional nonbiological caching/routing with equal budgets.
Browser SHA-256d activity and rendering FPS do not pass this gate.

### Gate 4 — independent WAN and cost validation: pending

Use independently operated nodes on different networks. Publish workload data,
reproducible baselines, failure tests, latency distributions and complete resource
accounting. Do not relabel loopback processes or synthetic traffic as production
BSC telemetry. No WAN rollout is asserted to be in progress without evidence.

## Full-node replacement remains a distinct research question

An alternative implementation would need observational equivalence:

    Transition_alt(state, transaction, block_context)
      == Transition_reference(state, transaction, block_context)

This includes post-state roots, gas, receipts/logs and applicable hard-fork rules.
A validator replacement additionally needs PoSA and network/security compatibility.
A small keeper process is not comparable to a full node with different duties.

This is a legitimate hypothesis to explore, not a feasibility proof derived merely
from the capabilities of a biological brain. Tests can find counterexamples;
passing a finite sample alone does not prove universal equivalence or efficiency.

## Reproducible research program

1. Declare the exact role, correctness, security and availability requirements.
2. Fix node/edge budgets, hardware, traffic, seeds and failure conditions.
3. Compare biological, rewired and conventional topologies without cherry-picking.
4. Include preprocessing, retries, client work, verification and retained storage.
5. Publish raw results and adverse outcomes, separating simulation from WAN tests.
6. Derive cost scenarios only after the relevant measurements exist.

## References

- [BNB Chain validator roles and PoSA](https://docs.bnbchain.org/bnb-smart-chain/validator/overview/)
- [BSC network identities and RPC endpoints](https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/)
- [MaleCNS source data and connectivity weights](https://male-cns.janelia.org/download/)
- [Eon's technical description and limitations](https://eon.systems/updates/embodied-brain-emulation)
- [EIP-191 signed data](https://eips.ethereum.org/EIPS/eip-191)

References motivate research; they are not endorsements or sources for the
unmeasured economic assumptions above.
