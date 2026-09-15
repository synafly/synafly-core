# SynaFly Lab contributor/agent boundaries

This is an independent research repository. Do not edit or deploy sibling projects.
Runtime/test commands and public claims are documented in README.md.

- Keep model state and wire formats deterministic; version semantic changes.
- Preserve data provenance and report limited sample coverage honestly.
- Do not relabel local Anvil tests as public BSC transactions.
- Do not request, read, commit or broadcast real private keys or seed phrases.
- Public-chain writes, new paid infrastructure and GitHub publication need explicit
  user-selected accounts/targets. No such deployment is implicit in a test run.
- Local experiments may terminate only processes that the experiment created.
- Keep hypotheses separate from measured and implemented features.
- Preserve negative results and matching-resource experimental baselines.

Checks: `python3 -m unittest discover -s tests -v`, `forge test -v`, both demo
scripts, `scripts/verify_history.py`, and `scripts/benchmark.py`.
