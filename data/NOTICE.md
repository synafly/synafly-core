# MaleCNS sample attribution

Source: MaleCNS collaboration — FlyEM / HHMI Janelia, University of Cambridge,
MRC Laboratory of Molecular Biology, and Google Research.

Dataset: male-cns:v1.0. [Download and documentation](https://male-cns.janelia.org/download/).
Data license: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
This derived sample remains under CC BY 4.0; the code's MIT license does not replace it.

Changes: select the first complete record batch of the pinned connection-weight
file; filter to annotated nonglial/nonartifact endpoints, remove self-loops, select
256 nodes by deterministic BFS, and retain 605 observed directed weighted edges.
See provenance.json and the extractor for exact rules, source range and hashes.

This is deliberately a limited, high-weight-biased excerpt, not a complete or
representative connectome. Other batches can contain additional edges between
these nodes. Source synapse counts are retained; computational signs and
parameters are not provided by the dataset and are illustrative in this release.
No institutional endorsement or relationship is implied.
