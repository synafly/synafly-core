# Non-normative RFC: separate witness bonds and future incentives

**Status: architectural exploration only. Not implemented, deployed or promised.**
The immutable `ContinuityRegistry` is an append-only infrastructure log, **NOT a token**.
The separate ecosystem token is `0x259dd071f40d96e61f2bcc663bfac6898d957777`.
This document does not change that token, authorize transfers, offer investment
returns, or introduce staking/rewards into the registry.

## Why decouple

The core registry verifies a fixed witness quorum and checkpoint ordering. It has
no balances, escrow, staking, mint/burn, distributions, slashing or committee
rotation. Adding economics would require a **different contract**, not a silent
upgrade or new interpretation of existing registry events.

A possible future `WitnessBond` could manage voluntary escrow and delayed exits,
while a separately reviewed admission policy identifies which registry/committee
and rules a bond references. Bond state must never be mistaken for consensus,
receipt truth or an entitlement encoded in the current registry.

## Candidate state machine (not code)

1. **Unbonded → Bonded:** an explicitly consented asset deposit into separate escrow.
2. **Bonded → Exit requested:** a recorded exit request starts a disclosed delay.
3. **Exit requested → Withdrawable:** delay and any legitimate unresolved challenge
   window finish. No unbounded operator veto or undisclosed extension.
4. **Challenge → Adjudication:** only an objective, previously specified violation
   could affect a bond. Outcomes must be reviewable and appeal/expiry rules explicit.

No durations, asset, minimum bond, reward rate or financial policy is selected here.
A liquidity/trading token's existence does not imply a holder has agreed to bonding.
Any future approvals/transfers require separate user consent and audited transaction UI.

## The missing proof problem

Two contradictory signatures over the same registry lineage/sequence may offer
inspectable equivocation evidence. By contrast, “the node did not save enough
upstream calls” is not objectively proved by its own receipts. A hash or quorum
signature cannot establish an unobserved counterfactual workload. Slashing based
on unverifiable self-reported savings would invite manipulation and unjust loss.

Before an economic design advances it needs a falsifiable challenge model, observer
independence, liveness analysis, key-compromise policy, Sybil/collusion analysis and
published negative controls. A single operator controlling derived witnesses is
not decentralized enforcement, regardless of how many signatures appear.

## Compatibility and gates

- Core registry bytecode/configuration remains unchanged and token-free.
- No retroactive bond obligations for existing witnesses or token holders.
- Separate ABI/address/permissions/documentation, with clear registry/token labels.
- Independent contract review, property/fuzz tests, adversarial challenge tests,
  escape/withdrawal analysis and public testnet evaluation before any funding request.
- Fees, incentives, governance, regulatory considerations and operational costs need
  a separate reviewed proposal. Nothing here establishes actual savings or revenue.

Until those gates are met, the implemented system is a **single-operator permissioned
continuity log**, not a staking protocol, reward mechanism or multi-party consensus.
