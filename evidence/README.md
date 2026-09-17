# Immutable historical source snapshots

PR #10 retains the exact prior CLI/status bytes referenced by PR #8 and PR #9
release hashes. Their lock entries now point to these snapshots with the same
hashes. They are historical evidence, not current commands or deployment status.
Old measurements are not reclassified as tests of new code.

The current infrastructure contract is an append-only continuity log, NOT a token.
The separate ecosystem token is `0x259dd071f40d96e61f2bcc663bfac6898d957777`.
No archive describes an automatic mainnet deployment or proves economic savings.
