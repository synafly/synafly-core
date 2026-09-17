# Quorum pipeline: recorded local EVM results

**Network: LOCAL ANVIL ONLY.** Chain ID 97 here is a development setting, not a
public BSC Testnet deployment. No public transaction, real wallet or real funds
were used. Base commit: `149f028a3e1b59494c3e072759608e79a50f24cd`.

## Completed execution

- Deployed the unchanged `ContinuityRegistry.sol` through an actual creation transaction.
- Verified deployed runtime against the compiled artifact with immutable threshold patched.
- Started three separate daemon processes and exercised real directed HTTP peer-cache reuse.
- Captured 13 receipts from role 0: peer reuse, coalesced reads and cache hits, including
  a follow-up hit after code-matched FlyHash prefetch at a new pinned block.
- Three distinct Anvil development accounts signed deterministic checkpoint approvals;
  a sorted subset of two met the 2-of-3 threshold. A fourth, non-witness account relayed.
- Committed two consecutive Merkle checkpoints and verified both full transaction
  receipts, exact `CheckpointCommitted` events, gas and checkpoint-block registry heads.
- Broadcast one actual replay transaction; it reverted with status zero and emitted no logs.

This is a complete local contract-integration loop, not only unsigned JSON or an
ABI mock. The committee is still controlled by one test operator. Signatures attest
receipt integrity/continuity, not independently observed real-world cost savings.

## Actual transaction evidence

Registry: `0x057ef64e23666f000b34ae31332854acbd1c8544`

Runtime Keccak-256: `0xed957041b9c4572db0f00efdfab015bb41246d259230602f5891ca4e9492746c`

| Operation | Local transaction hash | Status | Gas used |
|---|---|---|---:|
| Deploy | `0x670fad3ac723c3c996d1f360d165ad0afc670a91cd760b7c96e7712cf289ca5d` | 1 | 840,256 |
| Checkpoint 0 | `0x2353ffbd5e792b1b5b4d357fe12ddd02505f33422587a3a20358ef8cf1a2cdbc` | 1 | 136,211 |
| Checkpoint 1 | `0xcf566a693fc1de1c1e51563ddad5d3a5929593804993a4eb2c0f38c8a45a08ec` | 1 | 63,112 |
| Replayed checkpoint 0 | `0x87b5d4e7acd2d516bd36a6b2ef9eac14a70e4da8182fed61c9cb751ff195ec27` | 0 | 35,130 |

These hashes refer to the ephemeral local chain; they are not BscScan links.
Gas is the actual EVM gas consumed in this run, not USD cost or a BSC savings estimate.

Event topic: `0xe7c2fb10036eb4e3a191f67d07d52241c5fc608fc85e34bd48dfeaac5b59b5b3`

The full receipts include transaction/block hashes and indices, sender, destination,
status, gas, effective gas price, logs/bloom and type as returned by Anvil. Full
transaction calldata, signatures, registry runtime, preflight replies and verification
wire replies are retained in [the raw report](../results/quorum-pipeline.json).

## Batches and roots

| Checkpoint | Receipt sequence range | Count | Merkle root | Tick |
|---|---|---:|---|---:|
| 0 | 0–5 | 6 | `0x12e072f4fbebfd49ea20b08e93d900c7ec49b0fce38195e708ff16607c5e2e58` | 0 |
| 1 | 6–12 | 7 | `0xa35fdbff678ff1395a407adcb649f5723cd5a626e9ee8c6e50ad26c3a1d4aab9` | 13 |

The second commitment parent equals the first Merkle root. Receipt-chain parents
remain individual receipt hashes, so the two sequencing domains are not conflated.
Every leaf has a checked index/count-bound inclusion proof. A new run may have
different timestamps/lineages/roots; fixed input bytes produce the same root.

## Exact negative EVM boundaries

| Case | Expected contract error | Observed revert data |
|---|---|---|
| changed_graph | `ModelChanged` | `0x8cfc5606` |
| cross_chain | `UnknownWitness` | `0xc8b28b1b` |
| cross_contract | `UnknownWitness` | `0xc8b28b1b` |
| duplicate_signer | `SignersNotOrdered` | `0xb550c570` |
| high_s | `InvalidSignature` | `0x8baa579f` |
| insufficient_quorum | `InsufficientQuorum` | `0x50884582` |
| replay_0 | `WrongParentOrSequence` | `0x998f0c35` |
| replay_1 | `WrongParentOrSequence` | `0x998f0c35` |
| unsorted_signers | `SignersNotOrdered` | `0xb550c570` |
| wrong_parent | `WrongParentOrSequence` | `0x998f0c35` |

These ten cases executed the real compiled contract using local `eth_call`, with
the expected exact selector and unchanged head recorded. A separate **broadcast**
replay transaction has status 0 and no event. A receipt does not itself contain the
custom-error return data; the selector observation and failed-transaction receipt
are distinct pieces of evidence. No rollback of a never-written field is claimed.

The array getter termination check was also verified against actual compiler
behavior: `witnesses(3)` returns an empty revert for this three-member registry,
not a high-level array panic. Failed/unavailable RPC is not accepted as equivalent.

## Offline verification and regression

- 159 Python tests: the prior 128 plus 31 new quorum/cluster cases.
- The existing 10 Foundry tests pass offline, including 128 fuzz runs.
- The focused `testQuorumCommitsAndAdvances` was run with `-vvvv`; it reaches
  `ContinuityRegistry.commit`, emits the checkpoint events and reads the advanced head.
- CI verifies real secp256k1 recovery and Merkle proofs against recorded Anvil
  data, exact ordered wire replay, ABI dynamic offsets, low-s/v rules, duplicate
  and insufficient votes, domain replay, receipt/bundle mutation, canonical block
  checks and the three-process owned Python wire cluster.
- A counterfactual changing the expected duplicate-signer selector to
  `UnknownWitness` fails the audit. A separate actual-Anvil rerun with that deliberately
  wrong expectation also fails at the EVM boundary, as recorded in
  [the counterfactual report](../results/quorum-pipeline-counterfactual.json).
  Positive status alone is not the criterion.
- Earlier source/report locks stay unchanged and pass their verifier.

```sh
python3 scripts/verify_quorum_pipeline.py
python3 scripts/verify_quorum_pipeline.py --local-anvil --out .local/new-evm-run.json
python3 scripts/run_mesh_cluster.py --demo --ports 0 0 0
python3 -m unittest discover -s tests -v
forge test --offline -v
```

The default verifier is an **offline recorded-wire audit**, not a fresh EVM run.
Only the explicit `--local-anvil` command deploys and broadcasts, exclusively on
a process it creates. No Anvil/compiler/global wallet environment is needed by CI.

## Remaining boundaries

- No public BSC Testnet/mainnet registry deployment or broadcast was performed.
  Testnet preparation exports unsigned calldata; external wallets/relayers remain user-managed.
- The three daemon processes and committee development accounts share one host/operator.
- Witness approval checks receipt integrity; it cannot detect coordinated fabrication
  of otherwise consistent traffic/savings claims. No economic reward mechanism is present.
- Storage values and chain confirmations remain provider-trusted; no consensus
  light client, state proof, witness rotation or permissionless committee is implemented.
- Pure-Python signature recovery is research code, not an external cryptographic audit.
- Receipt/checkpoint bytes must be retained off-chain; a root cannot restore lost data.

See [protocol, usage and signing details](quorum-pipeline-design.md).
