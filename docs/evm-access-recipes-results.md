# Access recipes and FlyHash slot retrieval: results

## What was achieved

The standalone runner captured **128 actual local EVM training traces**, extracted
**96 supported trace entries representing 8 unique recipes**, and froze predictions
for **256 new calls × 5 methods = 1,280 retrieval cases** before inspecting their
held-out traces. It then checked actual EVM executions after optional slot reads.

Guarded Union, Exhaustive NN and FlyHash all matched the complete footprint on
**160 supported held-out calls**, emitted no extra slots, and abstained on the
remaining **96 out-of-scope calls**. This demonstrates reusable symbolic slot
recipes on the declared fixtures. It does **not** establish FlyHash as more
accurate or cheaper than the guarded conventional baseline.

[Engineering design and scientific prior](evm-access-recipes-design.md) ·
[Complete captured corpus](../data/evm-access-recipes.json) ·
[Results and counters](../results/evm-access-recipes.json) ·
[Source/protocol lock](../results/access-recipes-release-lock.json)

## Dataset and causal separation

The bytecode and call schema are authored synthetic fixtures, not mainnet traffic
or MaleCNS data. They cover direct and nested mappings, caller-keyed storage,
read-modify-write, four guarded branches, unsupported partial memory writes,
state-dependent keys, and a same-address code upgrade.

Training uses 16 calls per family. The 32 rejected traces are from the MSTORE8
and state-dependent families. Upgrade training shares the old direct-mapping
recipe; evaluation uses different bytecode and must not reuse it.

The 256 held-out calls use new keys/callers and route descriptors. All predictions
are frozen before held-out footprint discovery and state seeding. The later
observations are used for evaluation, not catalog updates or projection tuning.
Catalog and prediction fingerprints remain unchanged through the run.

The 8 unique recipes are direct, nested, caller, write and four branch recipes.
96 accepted observations must not be advertised as 96 distinct learned functions.

## Full comparison

Each method is evaluated on the same 256 calls and 288 total reference slot
occurrences. Counts are sums of per-call unique slot sets, not globally unique
storage addresses.

| Method | Exact complete footprints / 256 | Abstained | Correct slots | Missed slots | Extra slots | Unique-recipe materializations |
|---|---:|---:|---:|---:|---:|---:|
| Guarded Union | 160 | 96 | 160 | 128 | 0 | 256 |
| Exact Descriptor | 0 | 256 | 0 | 288 | 0 | 0 |
| Exhaustive NN | 160 | 96 | 160 | 128 | 0 | 240 |
| FlyHash | 160 | 96 | 160 | 128 | 0 | 238 |
| Stale Slots ablation | 0 | 96 | 0 | 288 | 160 | 160 previous-slot selections |

Interpretation:

- For the three successful recipe methods, supported-case exactness is 160/160,
  but **all-case exact coverage is 160/256 = 62.5%**. Their slot recall over the
  entire corpus is 160/288 = 55.56%. Do not call this universal 100% prediction.
- The 96 abstentions comprise unsupported partial memory writes, state-dependent
  key derivation and changed-code calls, 32 each. Fallback authoritative execution
  still proceeds; abstention is not a fabricated successful prediction.
- Exact Descriptor intentionally encounters unseen route descriptors (training
  0–15; held-out 16–47). Its zero coverage is a limitation of that exact lookup,
  **not** proof that conventional methods cannot generalize. Guarded Union can.
- The stale concrete-slot ablation emits 160 wrong extra slots and finds no actual
  slots. Parameterization matters in this fixture; storing the old addresses is
  not enough when arguments/callers change.
- All materializing methods deduplicate identical recipes after candidate
  selection. FlyHash considers 238 unique recipes versus NN's 240 and Union's
  256. The counter **excludes** signature construction, scans, postings, hash
  subprocesses and memory. It is not a latency, CPU, IOPS or net-cost measurement.
- Similarity top-k can drop a useful recipe on other inputs. The current small
  corpus does not establish a robust advantage over a guarded dictionary, nor a
  biological advantage over conventional indexing.

## Non-invasive execution evidence

For every one of the 1,280 retrieval cases, the runner performs only the proposed
read hints and then reruns the unchanged EVM call at the same pinned local block.
Captured **return bytes, gas, failure status and actual slot set** match the
baseline. The corpus now retains every post-prefetch observation, so the offline
verifier compares evidence rather than reconstructing an assumed `true` flag.

This invariant holds even for the inaccurate stale-slot ablation. **Unchanged
EVM output is not evidence that the prediction was correct.** It shows why hints
must remain separate from authoritative execution.

After evaluation, all 194 initialized storage values are read from current state
and checked against their deterministic initial values, and the current head
still matches the pinned block. The fixture wrapper rejects setup/state-mutating
methods after sealing. SSTORE instructions are executed only inside discarded
call simulations; no signed or broadcast transaction is made.

Local fixture preparation does change its own state with `anvil_setCode`,
`anvil_setStorageAt` and `evm_mine`. Those calls are explicitly counted and must
not be described as a write-free setup or a public-chain experiment.

### Successful local RPC accounting

| Method | Successful replies | Scope |
|---|---:|---|
| `eth_chainId` | 1 | Owned fixture readiness/identity |
| `eth_getBlockByNumber` | 3 | Genesis, pinned block, final current head |
| `anvil_setCode` | 10 | Original programs, code upgrade, one diagnostic fixture |
| `debug_traceCall` | 1,921 | 128 training + 256 seeding discovery + 256 baselines + 1,280 post-hint calls + 1 dynamic-jump diagnostic |
| `web3_sha3` | 9 | Cross-check exact Keccak against Anvil |
| `eth_getCode` | 8 | Held-out runtime code identities |
| `anvil_setStorageAt` | 194 | Owned local preparation only |
| `evm_mine` | 1 | Seal/pin the prepared fixture |
| `eth_getStorageAt` | 834 | 640 optional prefetch reads + 194 final state readbacks |

Counters count successful local replies; failed readiness connection attempts
before Anvil accepts connections are not included. After sealing, the complete
RPC set is **1,536 trace calls, 834 storage reads and one head check**, with no
setup/write method. The 640 optional reads are additional work; they are not
presented as avoided RPCs or useful native-client cache hits.

## Offline CI versus real reproduction

The CI test path requires Python 3.12+ only. The nine new tests use recorded HTTP
trace replies and **305 finite recorded Keccak replies**, with Anvil/cast subprocess
execution explicitly forbidden by mocks. Unknown wire requests/hash inputs fail
instead of inventing results. The package's default hasher for new materialization
still uses real installed `cast keccak`; no custom crypto or SHA3 substitution
was introduced.

Offline verification rebuilds the catalog/predictions and recomputes all result
accounting. It validates corpus/source hashes, raw post-prefetch observations and
storage readbacks. **It does not rerun the EVM or independently recompute Keccak
by default.** `--with-cast` independently recomputes all 305 recorded hash answers
without a public RPC. The actual local run separately cross-checked nine vectors
against Anvil's `web3_sha3`.

The standalone capture preserves the prior study's **128 training traces, 256
reference results, all 1,280 per-case result rows, summaries, catalog hash and
prediction hash exactly**. Release changes remove an unrelated training-node
initializer, add a sealed phase, and retain richer evidence and wire fixtures.
New source/corpus hashes and local block context are recorded rather than
pretending these packaging changes never occurred. No historical exploratory
network/full-brain module is a runtime dependency.

Local release checks passed **102 Python tests (93 existing + 9 new)** on Python
3.12.13 and 3.14.5, plus **10 Foundry tests** with 128 fuzz runs: **112 distinct
tests** across the two suites. The Python 3.12 run used a PATH without Foundry,
and the nine new tests additionally forbid Anvil/cast subprocess execution. The
real Anvil capture and independent 305-vector cast check are separate evidence,
not work performed by the offline CI fixtures.

## Reproduce

Audit committed artifacts without Anvil, cast, keys or network access:

```sh
python3 scripts/verify_access_recipes.py
python3 -m unittest discover -s tests -p test_access_recipes.py -v
python3 -m unittest discover -s tests -v
```

For an independent hash check with installed cast:

```sh
python3 scripts/verify_access_recipes.py --with-cast
```

For a fresh owned local EVM capture with installed Anvil and cast, preserve the
committed evidence by using separate paths:

```sh
python3 scripts/demo_access_recipes.py \
  --out .cache/access-recipes/replay.json \
  --corpus .cache/access-recipes/corpus.json
python3 scripts/verify_access_recipes.py \
  --report .cache/access-recipes/replay.json \
  --corpus .cache/access-recipes/corpus.json --with-cast
```

These scripts never opt into a public chain. Results are version-specific research
observations, not a production API, a formal proof or an independent audit. The
corpus consists of original synthetic programs/traces under the project's MIT
license, not a redistribution of biological connectome data.

## Next evidence gate

A native execution-client integration would need to show that optional hints
warm the **same** physical cache used by authoritative execution, preserve state
and gas semantics under failures/mispredictions, and reduce total work after
including retrieval, hashing and wasted reads. Representative workloads,
realistic cache budgets and matched conventional baselines are still required.
No BSC hardware-cost saving, mainnet slot-prediction accuracy, autonomous neuronal
learning or FlyHash superiority is established by this release.
