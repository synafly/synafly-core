# Reproduce the graph sample

Normal core tests use the committed small JSON graph and require no downloads.
To independently reproduce it, use Python 3.12 and `requirements-data.txt` in a
separate environment. Only the optional extraction tool needs PyArrow.

1. Obtain the official body-annotation file from the
   [MaleCNS download page](https://male-cns.janelia.org/download/).
   Its expected SHA-256 is recorded in data/provenance.json and checked by the extractor.
2. Run `python3 scripts/fetch_prefix.py`. It fetches only bytes 0–521799 of the
   pinned official weight-file generation, demands HTTP206 and an exact Content-Range,
   and verifies the previously recorded prefix hash. It does not fetch the full graph.
3. Run the extractor with the two explicit paths:

```sh
python3 scripts/extract_malecns.py .cache/weights-prefix.arrow /path/to/body-annotations.feather
```

The prefix ends after a complete 65,536-row Arrow record batch. The original whole
file has 1,051,241,946 bytes; its full-file hash has NOT been independently verified
by this excerpt workflow. The source range, generation and local prefix digest
are pinned; a partial file is never mislabeled as a fully verified dataset.

The exact transformation is in the script and manifest. We remove glial/artifact
endpoints and self-loops, choose 256 annotated nodes by deterministic traversal,
and retain 605 observed edges. This is a convenience sample biased by the source
batch's ordering, not a dataset-wide neuroscientific inference.
