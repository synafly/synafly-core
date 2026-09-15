# Checkpoint protocol v1

## Run identity

Canonical JSON uses sorted object keys, no insignificant spaces, ASCII-escaped
strings and integer-only numerical values. Duplicate JSON keys, floats, booleans,
nulls and nonfinite values are rejected at the checkpoint protocol boundary.
Hash = SHA-256 of those bytes, represented as lowercase hex without `0x`.

The run specification commits to the graph, exact model parameters and stimulus
seed. Its digest is the lineage identifier. Node IDs and wall-clock timestamps
are excluded from model state so hosts can reproduce the same result.

## Dynamics

Synchronous integer ticks. Only previously spiking cells transmit along outgoing
weighted edges. Nonrefractory potentials decay by integer `7*v//8`, receive
external input plus `8*synapse_count` for each arriving transmission, clamp to the
configured maximum, and spike above threshold 1024. A spike resets potential and
starts a two-tick refractory interval. All connections are treated as excitatory.
These choices are demonstrative, not fitted physiological parameters or physical
units. There is no plasticity, real sensory decoder or MuJoCo body loop.

## Persistence

Each checkpoint includes run/graph/model hashes, sequence, tick, parent hash,
full integer state, input frames and separate state/input hashes. A keeper must
replay the transition and match the entire canonical checkpoint before import.
Rehashing a tampered state does not make its transition valid.

SQLite uses WAL and FULL synchronization. An import batch and its head update
commit atomically. Content hashes are checked on retrieval; the selected history
is replay-verified on reopen. Duplicates are idempotent. A stale or conflicting
peer cannot replace the local head. Fork resolution is deliberately not hidden
behind a longest-chain rule; an operator must resolve conflicting histories.

## Peer endpoints

- `GET /health`, `GET /v1/spec`, `GET /v1/head`
- `GET /v1/checkpoints/<sha256>`
- `POST /v1/advance`: `{expected_parent, inputs}`
- `POST /v1/sync`: `{peer}` with a configured peer name, never an arbitrary URL

Each node has its own operator bearer token in `LAB_ADMIN_TOKEN`; tokens are not
shared through the protocol. Public reads allow independent validation. Writes
require local operator authorization. Remote peers require HTTPS and do not follow
redirects. This is a configured federation, not Sybil-resistant open membership.

Research bounds: 2048 graph nodes, 50000 edges, 16 frames/checkpoint, 2048 stored
objects, 256 checkpoints/sync, 128 KiB POST bodies, 16 HTTP handlers and bounded socket
and peer timeouts. Storage/pruning beyond these bounds is future work.

## EVM binding

The registry stores SHA-256 checkpoint commitments as bytes32 values. Its signing
message is a Keccak-256 ABI-encoded domain including contract address, EVM chain ID,
lineage, graph/model hashes, checkpoint/parent and sequence/tick. Ethereum personal
signing is applied to that message. Witnesses are immutable, distinct and ordered;
signatures must be low-s and from a threshold of known witnesses.

The registry rejects gaps, old parents, changed graph/model IDs, duplicate signers
and cross-contract/chain signature reuse. It neither executes the model nor stores
full state. Quorum attestations depend on honest independent witnesses. An
operator-supplied RPC receipt check validates exact event fields, receipt success,
block hash and confirmation depth; it is not a standalone BSC consensus proof.
