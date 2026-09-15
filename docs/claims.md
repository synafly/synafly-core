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
| BSC public-chain checkpoint exists | Not deployed | deployments/bsc-testnet.json and bsc-mainnet.json remain explicitly empty |
| On-chain hashes guarantee perpetual recovery | False without retention assumptions | Keepers must preserve bytes; hashes alone cannot restore unavailable data |
| Full fly brain or consciousness has been uploaded | Not implemented or claimed | Small positive-weight toy model; no fitted physiology, learning or embodied control |
| BSC node roles can be offloaded efficiently | Hypothesis | docs/bsc-feasibility.md defines roles, costs, equivalence and falsification conditions |
| BSC validators have been replaced / $20M saved | Not implemented or demonstrated | No full-node, PoSA or fleet-cost benchmark |
| The entire live website is open source | Not claimed | This is an independent partial research release |

Appropriate release wording after publication:

> We have open-sourced a research prototype for verifiable model-state continuity:
> connectome-derived dynamics, peer checkpoint recovery and an EVM commitment registry.
> Current evidence is local. Public BSC anchors and independently operated keepers
> are subsequent milestones. Additional components will be released as they mature.

Do not replace these distinctions with an unqualified "immortal on BSC is already
live" statement. Hypotheses may be published before validation, but must remain
identifiable as hypotheses.
