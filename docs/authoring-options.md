# Writing a week

[Pizza Week](../examples/pizza_week.json) is the readable author file. It contains `noise`, reusable `channels`, and seven `days` sections, from `monday` through `sunday`. Empty days are `{}`.

Define each chat once under `channels`, with its source `type`, `participants` and optional group `name`. A scene then needs only `id`, `channel`, a human-readable `time`, a short `scene` description and inline English `facts`. Its channel key also identifies its thread. The same contact name resolves to the same synthetic source identity across DMs and groups; silent group members remain members.

```json
{
  "channels": {
    "devon": {"type": "imessage", "participants": ["Me", "Devon"]}
  },
  "days": {
    "monday": {
      "scenes": [
        {"id": "invite", "channel": "devon", "time": "7:30 am",
         "scene": "A conversation about getting pizza.",
         "facts": ["They agree to Domino's on Friday. Maria is coming too."]}
      ]
    }
  }
}
```

This fragment belongs in a complete seven-day week. Facts stay next to the scene; there is no top-level fact catalog or fact-ID matching. Assistant turns have their own English `expect` descriptions; see [conversations](flexible-checks.md).

`noise` is `low`, `medium` or `high`. Low is the shortest natural exchange (normally 4–12 messages). Medium targets 20–40 messages per person per conversation per day. High targets 600–800 per person, including unrelated topics, facts and side plans. Multiple scenes in the same chat/day share that target. Larger exchanges use bounded chunks; messages are never padded, copied or truncated to meet a count. Start with low. An optional `until` sets the scene's ending wall time; high otherwise continues through the rest of its day, respecting the next scene in that chat. Authored start times remain unchanged.

Concrete dates are assigned during preparation. `--week-start` selects a Monday; without it, preparation uses the current week in the configured timezone. Eastern defaults to `America/New_York`, including daylight saving time. Dates and offsets are frozen in the compiled corpus.

```sh
python -m pacbench.prepare examples/pizza_week.json --week-start 2026-10-05 --output runs/pizza-prepared
python -m pacbench.preparation_audit runs/pizza-prepared
python -m pacbench.native_pilot --prepared runs/pizza-prepared/week.json --providers memcal --output runs/pizza-current
```

Preparation writes one portable `week.json`, containing generated messages, verification records, questions and evidence-cited judging contracts. Provider execution imports that file without authoring or verifying the messages again. Keep it to reuse across providers and runs. `--resume` reuses unchanged verified scenes when fixing an interrupted preparation; changed scene requirements need new verification. Failed scenes are recorded, dependent source continuations skip, and independent scenes continue. Incomplete preparation cannot become a runnable frozen corpus.

The minimal [Pizza Day](../examples/pizza_day.json) scene format remains useful for smoke tests. Preparation always runs separately from Hermes and native provider ingestion. All paid work goes through the shared gateway.
