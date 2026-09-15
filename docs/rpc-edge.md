# Read-only RPC edge profile v0.2

## State semantics

The edge forwards only four methods: `eth_getBalance`, `eth_getTransactionCount`,
`eth_getCode`, `eth_getStorageAt`. Addresses are 20-byte hex data. Storage positions
are canonical hex quantities up to 256 bits. Missing block selectors mean `latest`.
Hexadecimal letter case is normalized; values, rather than raw response spelling,
are compared. Mixed-case address checksum validation is not implemented.

An operator fixes one bare upstream origin, chain ID and genesis hash. Startup
checks both identifiers. Cache identity is the SHA-256 of a versioned encoding of
network identity, method, normalized arguments and block-selector semantics. RPC
request IDs are never part of a cache key; each response retains its caller's ID.

| Selector | Origin query | Cache / coalesce |
|---|---|---|
| Explicit `blockHash`, canonicality omitted or false | Same hash, false | Eligible |
| `blockHash` with `requireCanonical: true` | Same hash, true | Always bypass |
| `latest`, `pending`, `safe`, `finalized`, `earliest` | Same tag | Always bypass |
| Hex block number or `blockNumber` object | Same number | Always bypass |

This profile follows the state distinction in [EIP-1898](https://eips.ethereum.org/EIPS/eip-1898).
It does not track chain heads or invalidate orphaned immutable entries. An explicit
noncanonical-required hash continues to identify the same state after a reorg;
the origin must decide canonicality for canonical-required requests. Unsupported
hash queries fail; there is no silent downgrade to a number or `latest`.

Cached state can remain available until expiry after an origin prunes it. The
edge therefore does not promise to reproduce a later origin's pruning error.
It trusts the configured provider for state truth and does not verify trie proofs,
headers, finality, PoSA or execution results independently.

## Protocol and resources

- `POST /rpc`: JSON-RPC 2.0 requests; positional parameters only.
- Valid notifications execute without a response (HTTP 204). A batch may contain
  1–8 entries and executes sequentially; all-notification batches return HTTP 204.
- IDs: strings up to 64 characters, integers within the exact JavaScript integer
  range, or null. Fractional and boolean IDs are rejected. Duplicate JSON members,
  nonfinite numbers and ambiguous result/error envelopes are rejected.
- Request body: 16 KiB; upstream response body: 256 KiB; JSON nesting: 12.
- Default cache: 128 entries and 1 MiB of value bytes, TTL 60 seconds. Entry count
  also bounds key/object overhead; the byte counter is not total process memory.
- Default concurrent origin reads: 8; HTTP handlers: 16; listen backlog: 32.
- Cache misses share a future only for eligible immutable keys. Capacity failures,
  upstream errors and invalid results are not cached. Every follower is released
  on failure; later reads can retry. No automatic upstream retry is performed.
- Default origin timeout: 3 seconds, configurable in the Python API up to 10.
  A socket-shutdown deadline after connect also stops trickling headers/bodies.
  Platform DNS resolution is outside Python's socket-timeout guarantee.
- Direct HTTP(S) transport, no redirects. HTTPS certificate verification is on.
  Configured network proxies are unsupported for remote origins and cause an
  explicit configuration error; they are not silently bypassed.

`GET /health` exposes the research mode and configured network identity.
`GET /metrics` exposes aggregate counters, never addresses, cache keys or bodies.
The daemon listens only on loopback, checks the Host header, rejects Origin
headers and does not enable CORS. It has no public deployment, authentication,
TLS termination, Internet abuse controls or remote administration surface.

## Example

Run the daemon with an explicitly selected endpoint:

```sh
python3 -m synafly_lab.edge_server --upstream https://bsc-dataseed.bnbchain.org
```

An ordinary, **uncached** latest-state request:

```sh
curl http://127.0.0.1:8831/rpc \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_getBalance","params":["0x0000000000000000000000000000000000000000","latest"]}'
```

For reproducible caching, use a block hash obtained from the selected network and
the EIP-1898 object. The public probe automates this and compares values against
uncached reads. It is not an archival service or a transparent accelerator for all
wallet calls. The production browser experience does not use this research edge.

## Tests

`tests/test_edge.py` exercises selector isolation, network identity, caller IDs,
concurrent success/failure, byte accounting, TTL/LRU, overload, malformed replies,
redirects, trickling responses and reorg-sensitive bypass. `scripts/demo_edge.py`
runs actual HTTP against a separate daemon process for each cache/coalescing mode.

Wire behavior is a bounded profile of [JSON-RPC 2.0](https://www.jsonrpc.org/specification),
not a claim to implement every method or extension of a BSC full node.
