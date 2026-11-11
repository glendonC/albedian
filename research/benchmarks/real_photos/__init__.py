"""Real-photo corpus for Albedian's plate solver: openly licensed photographs by third parties, fetched by checksum.

The synthetic benchmark next door (`research.benchmarks.phone_photos`) renders frames whose truth is known exactly,
and a solve there never shows that a real photo solves. This package is the other half: real pixels from real
cameras and phones, taken by other people and published on Wikimedia Commons under CC0, public domain, CC BY or
CC BY-SA. It holds no ground truth yet: no photo here has a verified solution, and what a photo shows is recorded
only as the uploader's own statement.

The photos are not in git. `data/downloads.json` lists each file's URL, size, SHA-256, licence and author; the
files live in the shared `.cache/real-photos/`. Nothing tracked holds a photo's coordinates or capture time: those
stay in the files' own metadata, which the ingest readers read at run time.

Modules:

- `corpus`: the manifest (`corpus.json`), its loader and rules, the registry checks, the credit table rows, and
  `read_facts`, which reads a file with the upload path's own readers.
- `__main__`: `uv run python -m research.benchmarks.real_photos fetch` and `... status`.

The tiers, the selection rules and the credits are in docs/science/REAL_PHOTO_CORPUS.md.
"""
