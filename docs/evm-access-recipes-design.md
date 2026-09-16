# EVM access recipes and FlyHash-assisted retrieval

## Objective and non-goals

Extract bounded, parameterized storage-slot recipes from **executed local EVM
traces**, then retrieve/materialize advisory slot hints for new call inputs before
observing their traces. Predict **where to read**, never a returned state value.
The authoritative EVM call remains unchanged even when a hint misses or is wrong.

This is an opt-in research runner and an offline replayable artifact, not a native
BSC prefetcher, RPC daemon, mainnet corpus, full-brain simulation or performance
claim. It neither changes consensus/gas accounting nor uses the MaleCNS graph.
The core extractor, projection and fixture programs are preserved from the prior
local study; release changes isolate the node fixture and strengthen replay.

## Dataflow and trust boundaries

```text
Authored bytecode + designated training calls
  -> actual local debug_traceCall traces
  -> bounded symbolic extraction + branch guards
  -> code-hash/selector namespaced recipe catalog
  -> freeze catalog

New calldata/caller -> fixed descriptor -> retrieval -> guard checks
  -> exact Ethereum Keccak materialization -> optional slot-read hints
  -> unchanged authoritative EVM call
```

A code hash/selector match is a catalog namespace, not a storage-value cache key,
canonicality proof or guarantee of complete path coverage. The runner explicitly
checks current bytecode before querying. Returned state belongs to the pinned
local block and target address. A same-address code upgrade moves to an unknown
namespace and abstains instead of reusing old recipes.

Training and held-out traces come from an owned, zero-account, loopback-only Anvil
process with no fork. They are trusted fixture observations, not permissionless
execution proofs. The extractor checks trace/code/stack consistency but is not a
complete EVM interpreter or proof verifier. A bad prediction cannot replace the
subsequent authoritative execution.

## Bounded symbolic extraction

Track PUSH/DUP/SWAP, supported arithmetic/bit operations, caller/value/calldata
inputs, aligned MSTORE/MLOAD, bounded Keccak preimages, SLOAD/SSTORE slot expressions
and supported branch guards. Check known symbolic operands against recorded stack
values and Keccak preimages against trace memory. Guards filter observed paths;
they do not prove exhaustive coverage of all possible executions.

- Bytecode <= 4,096 bytes; calldata <= 256 bytes.
- Trace <= 512 logs, top-level frame only; symbolic stack <= 64 items.
- Aligned memory <= 512 bytes; each hashed slice is 32–128 bytes.
- Expression depth <= 16 and evaluation work budget 512.
- Recipe <= 16 accesses, 16 guards and 16,384 encoded bytes.
- Catalog <= 4,096 observations; materialized slot union <= 64 slots/query.

SLOAD values remain unknown; state-dependent slot keys/guards abstain. Unsupported
opcodes, nested/external calls, partial-word writes, dynamic memory offsets and
nonconstant jump destinations also abstain. The supported grammar is deliberately
small; this is not general Solidity or arbitrary ABI understanding.

## Ethereum Keccak versus FlyHash

These are different functions with different roles:

1. **Ethereum Keccak-256** computes code identities and exact storage preimages.
   New materialization uses installed `cast keccak` via bounded stdin/response
   handling, with a 4,096-entry cache. SHA3-256 is not substituted; no new
   cryptographic implementation is introduced. Subprocess hashing is not a
   production-throughput design.
2. **Fly-inspired sparse tags** select candidate recipes. The scientific prior is
   similarity hashing inspired by olfactory circuitry, as described by
   [Dasgupta, Stevens and Navlakha (2017)](https://www.biorxiv.org/content/10.1101/180471v1).
   The implementation is an engineered adaptation, not a claim of inventing
   FlyHash or reproducing the paper's experiments or biological energy use.

The fixture descriptor has **21 binary features**: complementary bit pairs for
8 low bits of the third calldata word, plus a 5-way length bucket. Project to
**256 rows**, each sampling **6 inputs** with seed **317**. Winner-take-all retains
**16 rows**, with deterministic index tie-breaking. Inverted postings rank
observations by active-tag overlap; retain at most 8 before guard evaluation.
No learned neuronal dynamics or online readout training is involved.

## Baselines and ablation

Every method uses the same frozen catalog and code/selector namespace:

| Method | Candidate selection | Role |
|---|---|---|
| Guarded Union | All namespace observations, then identical-recipe deduplication and guard/materialization checks | Strong conventional baseline |
| Exact Descriptor | Exact 21-bit descriptor match, then the same checks | Exact-lookup reference; weak on unseen descriptors |
| Exhaustive NN | Scan namespace vectors, exact squared distance, top 8 observations, same deduplication/checks | Matched non-FlyHash similarity baseline |
| FlyHash | Sparse-tag postings/overlap, top 8 observations, same deduplication/checks | Fly-inspired candidate retrieval |
| Stale Slots | Last observed concrete slot list, without parameterization | Negative ablation of symbolic recipes |

Deduplicate identical recipes for **all** materializing methods. Repeated training
observations are not additional unique templates. Candidate counts charge unique
recipes materialized, **not** descriptor construction, full NN scans, posting
traversal, hashing time, bytes, CPU, memory or physical I/O. Top-k truncation can
miss recipes; it does not certify coverage. Preserve all misses/extras/abstentions.

## Fixed experimental protocol

Eight fixture families: direct mapping, nested mapping, caller-keyed mapping,
read-modify-write mapping, four-way calldata branch, unsupported MSTORE8,
state-dependent key, and same-address code upgrade. The upgrade family trains
on the original direct-mapping code and changes code before evaluation.

- 16 training calls/family: **128 actual training traces**.
- Keys/callers separate across training and evaluation; routing words 0–15 for
  training, 16–47 for evaluation. The descriptor shift intentionally makes exact
  descriptor matching a weak reference, not a substitute for the guarded baseline.
- Freeze the catalog, then predict **256 held-out calls × 5 methods = 1,280 cases**.
- Only after every prediction is frozen, inspect held-out traces to seed a nonzero
  storage fixture. Those traces never enter the catalog or tune the projection.
- Seal the fixture, mine/capture the block, then compare baseline execution against
  every predictor's optional `eth_getStorageAt` reads followed by execution.
- Record actual post-prefetch return bytes, gas, failure status and accessed slots.
  Check current storage and the current head again after all evaluation calls.
- One extra actual trace of a calldata-computed jump is captured for offline
  rejection tests. It is counted separately from the 1,920 benchmark trace calls.

## Read-only evaluation, explicit local preparation

Preparing the **owned local fixture** uses `anvil_setCode`, `anvil_setStorageAt`
and `evm_mine`; it is not truthful to call preparation write-free. The wrapper has
no arbitrary remote URL and no transaction/signing method. After `seal()`, setup
methods are rejected **before transport**, and only bounded read/simulation
methods remain. Simulated SSTORE inside `debug_traceCall` does not authorize a
committed transaction. Current-state readbacks and the unchanged head check
supplement the method allowlist. No public keys, secrets, wallet or public-chain
write is used. This harness is not a general sandbox for arbitrary user programs.

## Two validation layers

**Offline CI/replay:** the saved corpus contains real training traces, frozen
predictions, baseline references, post-prefetch observations, storage readbacks,
a dynamic-jump wire example and finite recorded Keccak input/output pairs.
Unit tests replay exact HTTP envelopes through `RecipeNode`; unexpected requests
fail. Subprocess mocks forbid Anvil/cast execution. Ordinary verification uses
recorded Keccak answers and rejects unknown inputs: it checks logic, integrity
and accounting, **not an independent cryptographic computation or a new EVM run**.

**Opt-in local reproduction:** `demo_access_recipes.py` starts its own Anvil and
uses real `cast keccak`. Nine vectors cross-check cast against Anvil `web3_sha3`.
`verify_access_recipes.py --with-cast` recomputes every recorded hash locally; it
still does not prove mainnet truth or hardware savings. Python CI requires no
Foundry binaries or third-party packages for this module. No hidden public-RPC
call, automatic live probe, dependency download or timing claim is permitted.

Default verifier execution is read-only. For a new local run, use separate
`--out` and `--corpus` paths so the committed capture is preserved. Runtime
failures replace success with a non-success report rather than passing by file
existence. SHA-256 source/corpus/prediction bindings are reproducibility checks,
not signatures or independent audit certifications.
