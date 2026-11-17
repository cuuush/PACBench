# Pizza Week r2 dataset

The revised Pizza Week scenario contains 142 synthetic messages across seven
batches, 42 questions, and 188 grading criteria. The dataset uses
`America/New_York` scenario time. The manifest records the reviewed authored
spec and the checksums of this export.

- `sources/bundle.json` holds the exact message records, including email
  subjects, quoted email text, speakers, thread IDs, timestamps, and the roster
  visible at each message. `sources/*/messages.txt` holds the exact daily
  documents used for native document import. Ingest only the relevant files
  under `sources/` at each checkpoint.
- `evaluation/` holds the exact question wording, timestamps, session labels,
  grading criteria, source evidence IDs, and frozen follow-up policy. Keep
  this directory outside provider ingestion and answer context.

The original 87-file input hash was
`a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a`.
Its checksum map is preserved in `original_contract`. The original grading
files embedded model prompts and review receipts. These files export their
reusable grading fields and retain the original contract hashes as provenance.
`dataset_sha256` binds the exported payload separately. The old executor's
archive-bound launcher requires a dataset reader before it can use this layout.

Run `python -m pytest -q tests/test_pizza_week_r2_dataset.py` to check checksums,
source serialization, chronology, and grading coverage offline.
