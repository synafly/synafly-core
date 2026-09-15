# Progressive public release checklist

This research release is independent of the production website. Review the exact
source and evidence for each release; do not imply the whole production application
is open source solely because these research components are published.

Before the first release:

- Re-run the Python tests, recovery demo, history verifier, ablation and local EVM demo.
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

Code that can be released now: model/checkpoint implementation, keeper protocol,
registry contract, adapters, sample-data transform, tests and raw local results.
Future additions require their own implementation and evidence before being listed
as delivered: public BSC anchors, independent operators, archival retention,
model-fidelity adapters and role-specific BSC relay benchmarks.
