# Progressive public release checklist

This research release is independent of the production website. Review the exact
source and evidence for each release; do not imply the whole production application
is open source solely because these research components are published.

Before the first release:

- Re-run the Python tests, recovery demo, history verifier, ablation and local EVM demo.
- Reproduce the HTTP-edge and routing experiments and run `verify_evidence.py`.
  Public BSC probes are opt-in, bounded reads; keep failures and the exact tested
  source hashes. Do not rerun them automatically in every CI job.
- Review the claim ledger against raw results. Keep local-vs-public chain labels.
- Review MIT code licensing and separate CC BY4.0 data attribution.
- Review only the repository's explicit source files. Do not publish `.cache`,
  `.local`, `.codex`, databases, compiler outputs, wallet keys or environment files.
- Replace no scientific limitations with untested promotional promises.
- Verify repository ownership and use a project identity with a private noreply
  email for public commits.
- Publish a normal initial research release; do not invent earlier commit dates,
  adoption, partners, independent operators or audited status.
- Use release tags to distinguish working primitives from hypotheses and future work.

Code in the research release: model/checkpoint implementation, keeper protocol,
registry contract, adapters, sample-data transform, bounded read edge, separate
topology controls, tests and their scoped results.
Future additions require their own implementation and evidence before being listed
as delivered: public BSC anchors, independent operators, archival retention,
model-fidelity adapters, biological RPC peer integration and representative WAN
benchmarks. Controlled cache workloads are not evidence for fleet cost savings.
