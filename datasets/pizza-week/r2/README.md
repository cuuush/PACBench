# Pizza Week r2 dataset

Pizza Week uses 142 frozen synthetic messages in `America/New_York`. The
[v2 conversation spec](../../../specs/pizza_week_workflow_v2.json) compiles them
into 48 thread documents across seven nightly releases. The continuing-account
schedule has 49 turns and 192 grading criteria. The isolated control has 42
turns and 188 criteria. [workflow-v2.json](workflow-v2.json) binds both schedules
and the nightly projection to the exact archived hashes.

- `sources/bundle.json` holds the exact message records, including email
  subjects, quoted email text, speakers, thread IDs, timestamps, and the roster
  visible at each message. `sources/*/messages.txt` preserves the historical r2
  daily projection. The v2 compiler derives the thread documents from the
  same bundle and assigns each document its scheduled release.
- `evaluation/` holds the exact question wording, timestamps, session labels,
  grading criteria, source evidence IDs, and frozen follow-up policy. Keep
  this directory outside provider ingestion and answer context.

The original 87-file input hash was
`a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a`.
Its checksum map is preserved in `original_contract`. The original grading
files embedded model prompts and review receipts. These files export their
reusable grading fields and retain the original contract hashes as provenance.
`dataset_sha256` binds the exported payload separately.

From the repository root, compile both v2 schedules offline and compare their
full contents and nightly document hashes with the archived values:

```sh
python -m pytest -q tests/test_pizza_week_workflow_dataset.py
python -m pacbench.specs specs/pizza_week_workflow_v2.json
```
