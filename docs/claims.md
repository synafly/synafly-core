# Claim-to-evidence ledger

| Claim | Status | Evidence / boundary |
|---|---|---|
| Connection weights are real MaleCNS data | Implemented excerpt | data/provenance.json, pinned range and annotation hashes; full source hash not verified |
| Connections participate in the state update | Tested | event-driven outgoing-edge propagation; tests/test_core.py and results/ablation.json |
| Biological topology is superior for useful computation | Unproven | Ablation changes state; no accuracy/usefulness/efficiency advantage is established |
| State can be replayed identically | Tested locally | results/cross-python.json, CPython 3.12/3.14; five seeds across three graphs |
| A model can recover after its original keeper disappears | Tested locally | results/continuity.json; real processes/separate stores; graph and history recovered from surviving peer |
| Keepers are geographically/administratively decentralized | Not established | All test processes share one host/operator; deploy independently to test this |
| Registry verifies a distinct witness quorum | Tested locally | Solidity tests, exact revert trace check and local Anvil commit receipts |
| Registry proves neuronal correctness on-chain | Not claimed | Contract checks attestations, not execution; dishonest quorum remains a risk |
| BSC public-chain checkpoint exists | Deployed permissioned log, no economic claim | Confirmed mainnet deployment/genesis are recorded in deployments/bsc-mainnet.json. Infrastructure log only; NOT the ecosystem token. |
| On-chain hashes guarantee perpetual recovery | False without retention assumptions | Keepers must preserve bytes; hashes alone cannot restore unavailable data |
| Full fly brain or consciousness has been uploaded | Not implemented or claimed | Small positive-weight toy model; no fitted physiology, learning or embodied control |
| BSC node roles can be offloaded efficiently | Hypothesis | docs/bsc-feasibility.md defines roles, costs, equivalence and falsification conditions |
| A bounded read-only RPC edge is implemented | Implemented and locally tested | synafly_lab/edge_server.py; four methods; pinned noncanonical-required cache; dynamic/canonical-required bypass |
| Cache/coalescing reduces origin calls in the fixture | Measured for the stated synthetic workload | results/edge-http.json: identical outcomes; 40/25/33/18 origin calls including bootstrap; not biological or economic savings |
| Public BSC read compatibility was observed | Bounded read-only observation | results/bsc-read-probe.json: one provider, one block, 27 calls, no transactions; provider failures also recorded |
| PR #5 role adjacency is integrated into real peer-cache lookup | Implemented, locally tested | PR #8 directed one-hop HTTP mesh; configured trusted peers; no full-brain simulation, WAN deployment or superiority claim |
| The observed graph beats conventional lookup | Not established | results/routing-benchmark.json preserves fewer hits versus all eight rewired controls in the zero-failure fixture; contact-cost trade-offs reported |
| BSC validators have been replaced / $20M saved | Not implemented or demonstrated | No full-node, PoSA or fleet-cost benchmark |
| The entire live website is open source | Not claimed | This is an independent partial research release |

Appropriate release wording after publication:

> SynaFly's research release includes verifiable state continuity, a bounded
> read-only RPC edge and controlled topology comparisons. Offline experiments are
> reproducible; a separate public BSC read probe records compatibility, not consensus
> proof or deployment. Biological peer integration and independently operated WAN
> keepers remain subsequent milestones. Additional components will be released as
> they meet their own evidence gates.

Do not replace these distinctions with an unqualified "immortal on BSC is already
live" statement. Hypotheses may be published before validation, but must remain
identifiable as hypotheses.

## PR #8 additions

| Claim | Status | Evidence / boundary |
|---|---|---|
| Independent daemons exchange cached state over HTTP | Tested on loopback | `results/edge-daemon-verification.json`; two simultaneous mesh processes; neighbor exit falls back to origin |
| FlyHash predictions fill the edge slot cache | Tested on synthetic bytecode | Same-block code hash checked; unknown namespaces abstain; original `eth_call` still forwarded |
| Observed block transactions can trigger prewarming | Tested with owned wire origin | Before the first client slot read; not a mempool feed, future-state oracle or deployed PancakeSwap catalog |
| Receipts prove real economic savings | Not established | Unsigned local accounting chain, registry-shaped fields only; no witnesses or broadcast |
| Untrusted peer data is consensus-verified | Not implemented | Envelope validation and authentication do not prove storage truth |
| All 5,000 clients always succeed | Not claimed | Explicit capacity and negative controls; all failures remain in stress reports |
| Container runs in production | Not verified | Dockerfile supplied; Docker unavailable in development environment |

## PR #10 mainnet preparation and token isolation

The infrastructure registry is an append-only, single-operator permissioned log,
not an ERC-20/BEP-20 token, swap contract, staking contract or reward mechanism.
The separate ecosystem token is `0x259dd071f40d96e61f2bcc663bfac6898d957777`;
that address is never a deployment target, registry address or witness address.
Current mainnet status remains **not deployed** until real deployment and genesis
receipts each satisfy the verification pipeline. A fixed genesis fixture is test
content, not measured mainnet savings. See [deployment boundaries](bsc-mainnet-log.md).
