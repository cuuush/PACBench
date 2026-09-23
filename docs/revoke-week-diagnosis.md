# Revoke-week run diagnosis

The saved Memcal run `life-20260922T153714Z` contains ten Hermes turns. The
original `turns.json` did not save prefetch text, but `hermes-home/state.db`
contains the exact API user messages. Each one includes a `MEMCAL SNAPSHOT`
inside `<memory-context>` with the fictional date. The snapshot ID changes
after the plant reminder, dinner state, and gate-code updates. Turns 7–8 and
9–10 reuse the same ID because the brief content did not change. The full
snapshot was sent again on each turn. The explorer now reads this saved session
context; it does not infer a turn's context from the final `brief.md`.

At the September 8 18:00 cancellation turn, the brief said “nothing known”
about the week. Hermes searched the wiki and archive, checked the date, opened
the conversation, then wrote the declined dinner. Those five calls explain the
tool count. The archive search and conversation result exposed the September 9
10:00 cancellation email during the September 8 turn. The email was ingested
before that turn: `manifest.json` records its batch submission before the
Hermes message timestamp in `state.db`. This is a benchmark timing defect,
independent of brief refresh. The old runner ingested all evidence up to a
later checkpoint before replaying earlier user turns.

Both arms' original `validity.json` files say `valid`. The explorer preserves
those files and adds a derived timing audit, marking both historical arms
excluded from provider comparison. The life runner now interleaves evidence ingestion, scheduled
maintenance, and user-turn replay by fictional time; a regression test asserts
that a user turn cannot see a later email before a later checkpoint. A fresh
bounded life rerun is still needed to validate the live provider path.
