"""Historical Hermes context is recovered only from a matching saved turn."""

import sqlite3
from pathlib import Path

from scripts.build_explorer_data import (historical_temporal_audit,
                                         saved_hermes_contexts)


def test_saved_hermes_context_matches_turn_prompt(tmp_path: Path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE messages (id INTEGER, role TEXT, content TEXT, api_content TEXT, timestamp REAL)")
        conn.executemany("INSERT INTO messages VALUES (?, 'user', NULL, ?, ?)", [
            (1, "[Simulated now: 2026-09-08T18:00:00.]\n\nCancel dinner.\n\n"
                "<memory-context>MEMCAL SNAPSHOT [id=old]</memory-context>", 1790091436.0),
            (2, "[Simulated now: 2026-09-09T12:00:00.]\n\nIs it canceled?\n\n"
                "<memory-context>MEMCAL SNAPSHOT [id=new]</memory-context>", 1790091520.0),
        ])
        conn.execute("INSERT INTO messages VALUES (?, 'user', ?, NULL, ?)",
                     (3, "[Simulated now: 2026-09-09T13:00:00.]\n\nNo brief here.",
                      1790091600.0))
    turns = [{"prompt": "Cancel dinner."}, {"prompt": "Is it canceled?"},
             {"prompt": "No brief here."}]
    saved_hermes_contexts(tmp_path, turns)
    assert "id=old" in turns[0]["prefetch_context"]
    assert "id=new" in turns[1]["prefetch_context"]
    assert "prefetch_context" not in turns[2]
    assert turns[2]["hermes_received_at"]


def test_audit_flags_future_batch_ingested_before_earlier_replay():
    turns = [{"kind": "user-turn-replay", "at": "2026-09-08T18:00:00",
              "hermes_received_at": "2026-09-22T15:37:17+00:00"}]
    batches = [{"collected_at": "2026-09-09T10:00:00",
                "ingested_at": "2026-09-22T15:37:14+00:00",
                "observations": ["cancellation-email"]}]
    audit = historical_temporal_audit(turns, batches)
    assert audit and audit["valid_for_comparison"] is False
    assert audit["temporal_exposure"][0]["observations"] == ["cancellation-email"]
