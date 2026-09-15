# Security and trust boundaries

Research software, not an audited production protocol. Do not deposit funds.
There is no token, payout, buyback or key-management functionality.

- Keeper APIs bind to loopback by default. Remote operation requires deliberate
  operator deployment behind TLS, access controls and external rate limits.
- Admin bearer tokens belong in local environment variables, never in Git,
  command examples, screenshots or bug reports.
- Peers are explicit, bounded and independently replay-verified. They can withhold
  data, delay responses or propose different valid input histories. Forks fail
  closed instead of silently changing the selected history.
- Full data availability is an assumption: hashes and on-chain events cannot
  reconstruct missing checkpoints or graphs. Preserve multiple copies.
- The immutable registry committee is permissioned. A 2-of-3 committee tolerates
  one unavailable witness, not two colluding witnesses. Validators can falsely
  attest scientific correctness; the contract checks signatures, not biology.
- Chain ID alone does not identify the real public BSC network. Local Anvil tests
  are labeled local. Public receipt checks trust the configured RPC and do not
  implement a PoSA light client or finality proof.
- Python source/model upgrades can change semantics. Fork a new model/run version
  rather than silently changing the meaning of already committed state.
- Loopback demonstrations share a host and operator. Independent deployment,
  internet DoS tests, Byzantine consensus, witness rotation and archival economics
  remain unimplemented.

Before external publication, review the exact files and remove all runtime stores,
credentials and local logs. Follow the release checklist; do not treat CI green as
a security audit or a claim of biological/financial correctness.
