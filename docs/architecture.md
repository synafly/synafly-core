# Decision: state continuity, not an infrastructure-replacement claim

Status: implemented local research prototype; see the claim ledger for evidence. Production website is separate.

The September 2026 discussion of digital fruit-fly continuity concerns preserving
model state and interaction history so another host can resume it. This does not establish
that consciousness was uploaded, or that SynaFly is endorsed by any quoted person.

## Chosen scope

1. A small provenance-tracked excerpt of real MaleCNS directed connection weights.
2. An integer, synchronous, leaky integrate-and-fire-inspired toy state machine.
   Parameters and synaptic signs are illustrative, not a validated brain model.
3. Content-addressed checkpoints committing to graph, model, input and state.
4. Independently running peer nodes, separate stores, deterministic replay and
   explicit fast-forward recovery. No always-available central coordinator.
5. An optional EVM checkpoint registry with an immutable fixed witness committee,
   quorum signatures, ordered sequence and parent checks. It stores commitments,
   not complete neural data, and does not execute the neural model.
6. A local Anvil integration test. A chain ID of 97 on Anvil is NOT BSC testnet.

## Alternatives and boundaries

- Merely copying the website would not test state recovery; rejected.
- Full embodied whole-brain emulation is outside this release: no calibrated
  physiology, sensory feedback controller, learning model or body dynamics.
- Permissionless Byzantine consensus is NOT implemented. Nodes are independent
  processes, but the anchor registry uses an explicitly permissioned committee.
- Conflicting unanchored input histories are retained/rejected as forks, not
  silently resolved by longest-chain or highest-height heuristics.
- One remaining replica plus the graph/model can restore state. A hash on BSC
  cannot restore missing bytes if all replicas and backups disappear.
- No token, buyback, payout or user-private-key management is included.

## Acceptance evidence

- Replay produces byte-identical state across processes and stop/resume boundaries.
- Tampered state, wrong graph/model, malformed inputs and broken parents fail.
- Duplicate objects are idempotent; stale/forked peers cannot roll back a head.
- Three real loopback node processes: replicate, terminate original, recover a
  fresh node from a survivor, continue and compare with uninterrupted replay.
- Registry tests reject insufficient/duplicate/nonmember signatures, wrong parents,
  reordered sequence, graph/model substitution and replay across contract/chain.
- Local EVM commit is decoded against the corresponding off-chain checkpoint.
- Scientific/data limitations, negative results and network identity are explicit.
