# PancakeSwap: bounded, read-only BSC mainnet probe

## Scope and reproduction

This experiment uses the existing `ReadEdge` and `HttpOrigin`, unchanged. It adds
an opt-in script and offline tests, **not a trading client or a replacement BSC
node**. The network origin is fixed to the user-selected official public RPC:

- RPC: `https://bsc-dataseed.bnbchain.org`; chain ID **56** plus pinned BSC genesis.
- Pair: `0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae`, PancakeSwap v2 USDT/WBNB.
- Measured query: `eth_getStorageAt(pair, "0x8", blockHash selector)`.
- Python 3.12+ standard library only; no new dependencies, accounts or keys.

```sh
# Opt-in live run; keep the committed observation intact.
python3 scripts/probe_pancakeswap_live.py --timeout 10 --out .cache/pancakeswap-rerun.json
# Offline fixtures and committed-evidence integrity checks; no public RPC.
python3 -m unittest discover -s tests -p test_pancakeswap_probe.py -v
```

Without `--out`, the report is `results/bsc-pancakeswap-live-probe.json`.
Each run first replaces any prior success with `status: running`, then atomically
writes its outcome. Archive older attempts before reusing a path. Ctrl-C records
`interrupted` when caught; an uncatchable kill may leave `running`, never a new
success. Output-directory/I/O failures are errors, not evidence of a network pass.

## What is actually read

The [official v2 pair source](https://github.com/pancakeswap/pancake-smart-contracts/blob/master/projects/exchange-protocol/contracts/PancakePair.sol)
inherits the storage fields in
[`PancakeERC20`](https://github.com/pancakeswap/pancake-smart-contracts/blob/master/projects/exchange-protocol/contracts/PancakeERC20.sol).
Inherited nonconstant fields occupy slots 0–4, followed by factory (5), token0
(6), token1 (7), and packed reserves (8). Constants consume no storage slot.
For the 256-bit integer `W` returned at slot 8:

```text
reserve0 = W & (2**112 - 1)
reserve1 = (W >> 112) & (2**112 - 1)
blockTimestampLast = W >> 224
```

The script checks nonempty runtime code, records its SHA-256, and checks padded
address words against factory `0xca143ce32fe78f1f7019d7d551a6402fc5350c73`,
token0/USDT `0x55d398326f99059ff775485246999027b3197955`, and token1/WBNB
`0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c`. The
[official factory listing](https://developer.pancakeswap.finance/contracts/v2/addresses)
provides the v2 deployment reference. These are sanity checks against the trusted
provider, **not independently verified bytecode equivalence or storage proofs**.
No `eth_call/getReserves()` cross-check is claimed. This slot layout must not be
reused unverified for v3 pools, proxies or arbitrary pairs.

Reserves are raw integer **decimal strings**, not floats, USD values, token
`balanceOf` values or trading quotes. `blockTimestampLast` is the pair's last
reserve-update timestamp modulo 2^32, not necessarily the captured block time.
The decoder accepts zero and full uint112 boundaries; a live result alone is not
a guarantee of usable liquidity or trade execution.

## Fixed state, honest concurrency

1. Verify chain ID and genesis with the existing transport. Capture `latest`
   **once**, after bootstrap, retaining block number, hash and timestamp.
2. Pin every code/storage read to that hash. Measured reads use
   `requireCanonical: false`, per the existing edge's immutable-state policy.
   There is **no** silent number/tag fallback if EIP-1898 is unsupported.
3. Use two in-process `ReadEdge` instances: `coalesce=False` versus `True`;
   `cache_entries=0` in both. Both share the same verified HTTP origin.
4. Default: two rounds, four barrier-released local worker threads per mode.
   Mode order alternates. Every burst finishes before the next begins. The
   live path adds no artificial delay, warm cache or retry to create a result.
5. Compare all 32-byte words, not only decoded values, and reconcile edge
   `origin_reads`/`coalesced_waiters` with actual transport counters and the
   per-attempt RPC journal. JSON-RPC IDs need not match; state values must.
6. Run six negative-control reads through a fresh, cache-disabled single-flight
   edge: two sequential slot-8 reads, two distinct concurrent token slots, and
   two concurrent hash-pinned `requireCanonical: true` reads. All six must go
   upstream and return the corresponding expected word.
7. Query the captured block number again; require the same hash from the provider.
   A detected reorg or canonical-required read failure fails the run.

The [EIP-1898 selector](https://eips.ethereum.org/EIPS/eip-1898) fixes state identity;
`latest` here means **the provider's head at capture**, not a continuously fresh,
independently validated or finalized head. The final check is an observation at
that moment, not a consensus/finality proof or a permanent canonicality guarantee.
The two modes cannot disagree merely because trades advanced the head between
them. Existing edge tests separately cover different block keys and mutable-tag
bypass. No provider switch, multi-provider voting or state-proof verification is
implemented.

Offered concurrency is four local threads, not four guaranteed simultaneous
origin calls or a deployed WAN service. Single-flight followers wait for a leader;
they are not additional upstream workers. This probe exercises the actual edge
class and real outbound HTTPS, **not** an HTTP ingress daemon, mobile clients,
peer routing, admission queues or biological topology. Thread scheduling may
produce less overlap on a rerun. If any single-flight burst has no merged waiter,
the result is `inconclusive`, even if all values match; it is not auto-retried.

## Recorded observations — 2026-09-16 UTC

[Successful report](../results/bsc-pancakeswap-live-probe.json):
15:54:17–15:54:49 UTC, timeout **10 seconds**, captured block **122249045**
(`0x7495f55`), hash
`0xc1ae4ebbae6f5e7fab22b125d75fbe847105cfa130ab11f1c3516a2cb265984c`.

| Measured reserve-query workload | Direct | Single-flight |
|---|---:|---:|
| Offered/completed client reads | 8 / 8 | 8 / 8 |
| Upstream reserve reads | 8 | 2 |
| Coalesced waiters | 0 | 6 |
| Cache hits | 0 | 0 |
| Replies matching the same raw word | 8 / 8 | 8 / 8 |

Thus **16/16 sampled replies match (100% of this finite sample)**. The measured
repeated-reserve workload avoids **6/8 upstream reads (75%)** versus direct mode.
This is not a claim that 75% of all BSC traffic can be removed.

- reserve0 / USDT raw: `38016919084165903075903540`.
- reserve1 / WBNB raw: `53400004558912104451019`.
- Last reserve-update timestamp: `1789574062`.
- All six negative-control reads forwarded; final canonicality check matched.

Successful-run accounting: **24 RPC attempts** = 2 bootstrap + 1 head capture +
4 pool-identity reads + 10 measured reserve reads + 6 negative controls + 1 final
block check. Transport totals are **4 metadata calls + 20 state reads**. Its JSON
payload-byte counters exclude HTTP/TLS framing, DNS and transport retransmissions;
they are not measured wire bandwidth or physical database I/O.

The [first failed attempt](../results/bsc-pancakeswap-live-probe-attempt-1.json)
used the default 8-second timeout and failed at `eth_getCode` after **4 RPC
attempts**. It is retained, with no reserve-comparison success claim. The operator
then explicitly ran once more at the supported 10-second timeout. The two report
attempts total **28 calls**. A separate seven-call read-only compatibility precheck
preceded them; this task therefore made **35 public RPC attempts** including that
precheck. It is not included in either experiment's measured denominator.

## Bounds, failure behavior and claim limits

- Defaults: 4 clients × 2 rounds; allowed 2–8 clients, 1–3 rounds, timeout
  0.1–10 seconds. Worst-case attempt budget is `14 + 2 * clients * rounds`
  (default **30**, maximum **62**). Early failures use less. There is no daemon,
  polling loop, hidden retry or automatic CI public-network run. Public-provider
  [availability and rate limits](https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/)
  still apply; do not turn this into a load test.
- Only `eth_chainId`, `eth_getBlockByNumber`, `eth_getCode`, and
  `eth_getStorageAt` are allowed. No wallet, key, signing, transaction submission,
  paid service, gas expenditure or registry deployment is involved. The public
  provider can see ordinary RPC traffic and source IP. Network/CPU use is not zero.
- Existing transport bounds response bytes and validates IDs, JSON and read
  widths. It refuses configured proxies and does not follow redirects. Socket
  deadlines do not guarantee a hard wall-clock cap on platform DNS resolution.
  The probe does not bypass proxy, sandbox or TLS security settings.
- Unsupported/pruned state, rate limits, malformed replies, wrong identity,
  missing code, mismatches, detected reorgs and timeouts fail closed. Burst outcomes
  and attempted calls are retained. An attempt counter is client-side evidence,
  not proof that a provider received a timed-out call. Non-success returns a
  nonzero CLI exit; `running`/`interrupted` must never be treated as `passed`.
- Reports include UTC times, source SHA-256 bindings, raw words, decoded reserves,
  per-burst outcomes/metrics and RPC method/selector/result-digest journals. Offline
  tests check the successful artifact's bindings and accounting without rerunning
  the public probe. They verify internal consistency, not independent mainnet truth.
- The sample is one pair, one provider and one selected block. It establishes
  mainnet **read coalescing** for a deliberately duplicate workload. It does not
  establish global 100% correctness, production throughput, latency improvement,
  validator replacement, lower NVMe wear, GPU savings, $20M savings, a 90% ecosystem
  load reduction, a biological advantage or BNB Chain endorsement.
