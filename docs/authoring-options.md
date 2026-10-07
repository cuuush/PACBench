# Authoring source messages

Write scenes with plain-language `scene` and `facts` fields. Facts are desired assertions, not automatic truth: a writer produces natural dialogue, a verifier checks it, and the contract cites supporting messages before any provider runs. Optional `message_range` bounds are 2–50 per scene; reject invalid drafts rather than padding or truncating them.

`at` anchors a fictional local date and time; `timezone` defaults to `America/New_York`. Each day gets its seasonal offset. IDs, thread IDs, windows and message timestamps are derived when omitted. Give successive scenes the same `thread` to model changes in one conversation. The writer sees previous messages in that thread without instructions to recap settled facts.

`test_scene_id` selects the target when multiple scenes contain facts. Top-level `facts` can define the final claims across the whole verified dialogue. The generic `scene` plus `test_intent` form also supports model-proposed evidence-cited claims. Supplied fact wording is preserved.

```sh
python -m pacbench.prepare examples/pizza_day.json --output runs/pizza-prepared
python -m pacbench.preparation_audit runs/pizza-prepared
```

Preparation never executes Hermes or a provider. Select providers separately in `native_pilot`. The old SDK runner and Hermes ingestion handoff are removed. [Flexible checks](flexible-checks.md) supports arbitrary questions and expected facts. Keep writer, verifier and judge calls stateless and routed through the cost gateway; saved encrypted writer reasoning stays private.
