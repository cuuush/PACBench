import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench.semantic_experiment import (
    checkpoint_batches, daily_batches, expand_week_spec, group_threads, resolve_spec, run_experiment,
    tool_behavior,
)
from pacbench.harness import _summarize_tool_result
from pacbench.authoring import ScenePlan, judge_prompt, scene_prompt, spread_timestamps


def test_provider_tool_use_is_distinct_from_capability():
    memcal = SimpleNamespace(tool_calls=[{"name": "memcal_search_archive"}],
                             tool_results=[{"success": True, "text": "found"}], policy_blocks=0)
    hindsight = SimpleNamespace(tool_calls=[{"name": "tool_search"}],
                                tool_results=[{"success": True}], policy_blocks=0)
    assert tool_behavior("memcal", memcal) == {
        "provider_tool_attempts": ["memcal_search_archive"],
        "provider_tool_calls": ["memcal_search_archive"],
        "blocked_provider_calls": [],
        "blocked_other_calls": [],
        "retrieval_called": True, "policy_blocks": 0}
    assert tool_behavior("hindsight", hindsight)["retrieval_called"] is False


def test_group_status_uses_distinct_non_owner_speakers():
    messages = [
        {"channel": "imessage", "thread": "pizza", "sender": "Me", "from_owner": True},
        {"channel": "imessage", "thread": "pizza", "sender": "Devon"},
        {"channel": "imessage", "thread": "ducks", "sender": "Maya"},
        {"channel": "imessage", "thread": "ducks", "sender": "Devon"},
    ]
    assert group_threads(messages) == {
        ("imessage", "pizza"): False, ("imessage", "ducks"): True}


def test_daily_batches_send_only_new_messages_in_time_order():
    messages = [
        {"source_id": "day-2", "collected_at": "2026-09-10T18:00:00"},
        {"source_id": "day-1-b", "collected_at": "2026-09-09T18:01:00"},
        {"source_id": "day-1-a", "collected_at": "2026-09-09T18:00:00"},
    ]
    assert [(day, [m["source_id"] for m in batch])
            for day, batch in daily_batches(messages)] == [
                ("2026-09-09", ["day-1-a", "day-1-b"]),
                ("2026-09-10", ["day-2"]),
            ]


def test_checkpoint_batches_include_quiet_day_without_reingesting():
    messages = [{"source_id": "pizza", "collected_at": "2026-09-07T18:00:00"},
                {"source_id": "bowling", "collected_at": "2026-09-09T18:00:00"}]
    contracts = {day: object() for day in
                 ("2026-09-07", "2026-09-08", "2026-09-09")}
    assert [(day, [m["source_id"] for m in batch])
            for day, batch in checkpoint_batches(messages, contracts)] == [
                ("2026-09-07", ["pizza"]),
                ("2026-09-08", []),
                ("2026-09-09", ["bowling"]),
            ]


def test_writer_and_verifier_see_the_local_message_window():
    plan = ScenePlan(scene="Make an evening plan.",
                     at="2026-09-08T19:00:00", id="evening")
    assert "between 19:00 and 19:20 local time" in scene_prompt(plan)
    assert "between 19:00 and 19:20 local time" in judge_prompt(
        plan, [("Me", "Today is dragging")])


def test_blocked_provider_tool_is_not_counted_as_executed():
    turn = SimpleNamespace(tool_calls=[{"name": "mcp__hindsight__list_memories"}],
                           tool_results=[{"success": True,
                                          "text": "PACBENCH-BLOCKED: outside boundary"}],
                           policy_blocks=1)
    behavior = tool_behavior("hindsight", turn)
    assert behavior["provider_tool_attempts"] == ["mcp__hindsight__list_memories"]
    assert behavior["provider_tool_calls"] == []
    assert behavior["blocked_provider_calls"] == ["mcp__hindsight__list_memories"]
    assert behavior["retrieval_called"] is False


def test_deferred_recall_through_tool_call_counts_as_retrieval():
    turn = SimpleNamespace(
        tool_calls=[{"name": "tool_call", "input": {"calls": [
            {"name": "mcp__hindsight__recall", "arguments": {"query": "pizza"}}]}}],
        tool_results=[{"success": True, "policy_blocked": False,
                       "text": '{"memories":[{"text":"pizza at seven"}]}' }],
        policy_blocks=0)
    behavior = tool_behavior("hindsight", turn)
    assert behavior["provider_tool_attempts"] == ["mcp__hindsight__recall"]
    assert behavior["provider_tool_calls"] == ["mcp__hindsight__recall"]
    assert behavior["retrieval_called"] is True


def test_bad_deferred_batch_does_not_count_as_retrieval():
    turn = SimpleNamespace(
        tool_calls=[{"name": "tool_call", "input": {"calls": [
            {"name": "mcp__hindsight__recall", "arguments": {"query": "pizza"}},
            {"name": "mcp__hindsight__list_memories", "arguments": {}}]}}],
        tool_results=[{"success": True, "policy_blocked": False,
                       "text": '{"error":"local tools need separate calls"}'}],
        policy_blocks=0)
    behavior = tool_behavior("hindsight", turn)
    assert len(behavior["provider_tool_attempts"]) == 2
    assert behavior["provider_tool_calls"] == []


def test_blocked_bridge_identifies_underlying_recall_attempt():
    turn = SimpleNamespace(
        tool_calls=[{"name": "tool_call", "input": {"calls": [
            {"name": "mcp__hindsight__recall", "arguments": {"query": "pizza"}}]}}],
        tool_results=[{"success": False, "policy_blocked": True,
                       "text": '{"error":"PACBENCH-BLOCKED: tool_call"}'}],
        policy_blocks=1)
    behavior = tool_behavior("hindsight", turn)
    assert behavior["blocked_provider_calls"] == ["mcp__hindsight__recall"]
    assert behavior["blocked_other_calls"] == ["tool_call"]


def test_blocked_tool_result_is_not_successful():
    event = {"type": "tool_result", "tool": "mcp__hindsight__list_memories",
             "success": True,
             "result": {"error": "PACBENCH-BLOCKED: outside memory-tool boundary"}}
    result = _summarize_tool_result(event)
    assert result["success"] is False
    assert result["policy_blocked"] is True


def test_bridge_error_result_is_not_successful():
    result = _summarize_tool_result({"type": "tool_result", "tool": "tool_call",
                                     "success": True,
                                     "result": {"error": "Use one local tool per call"}})
    assert result["success"] is False
    assert result["policy_blocked"] is False


def test_invalid_spec_keeps_incomplete_experiment_artifact(tmp_path):
    root = tmp_path / "failed-run"
    spec = {"id": "bad", "scenes": [], "test_scene_id": "missing"}
    with pytest.raises(ValueError, match="at least one scene"):
        run_experiment(spec, root)
    status = json.loads((root / "experiment-status.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    assert status["state"] == "incomplete"
    assert status["stage"] == "spec validation"
    assert summary["failed_stage"] == "spec validation"
    assert json.loads((root / "spec.json").read_text()) == spec


def test_failed_run_audit_marks_completed_body_incomplete(tmp_path, monkeypatch):
    import pacbench.semantic_experiment as experiment

    root = tmp_path / "audit-failed"
    monkeypatch.setattr(experiment, "_run_experiment_body",
                        lambda spec, path, progress: {"state": "complete", "results": []})
    monkeypatch.setattr(experiment, "audit_run",
                        lambda path: {"state": "fail", "errors": ["duplicate input"]})
    summary = experiment.run_experiment({"id": "pizza"}, root)
    assert summary["state"] == "incomplete"
    assert json.loads((root / "run-audit.json").read_text())["errors"] == ["duplicate input"]
    assert json.loads((root / "experiment-status.json").read_text())["state"] == "incomplete"


def test_scene_clocks_and_ids_are_derived_from_one_occurrence_time(tmp_path):
    spec = {"id": "ice-cream", "scenes": [{
        "at": "2026-10-01T17:00:00", "scene": "Mike, Sarah and I get ice cream.",
        "facts": ["The meetup is at 5pm."]}]}
    scenes, target, resolved = resolve_spec(spec, tmp_path / "unused")
    assert target is scenes[0]
    assert scenes[0].id == "scene-01"
    assert scenes[0].thread == "scene-01"
    assert scenes[0].window_end == "2026-10-01T17:20:00"
    assert resolved["maintenance_at"] == "2026-10-02T08:00:00"
    assert resolved["question_at"] == "2026-10-02T09:00:00"
    assert "target_lines" not in resolved["scenes"][0]
    assert "id" not in spec["scenes"][0]


def test_week_authoring_derives_ids_and_next_morning_clock(tmp_path):
    spec = json.loads((Path(__file__).parents[1] /
                       "examples/pizza_day_week.json").read_text())
    assert "facts" not in spec and "scenes" not in spec
    assert all("id" not in row and "at" not in row and "thread" not in row
               for rows in spec["days"].values() for row in rows)
    expanded = expand_week_spec(spec)
    assert expanded["scenes"][0]["id"] == "monday-imessage-devon"
    assert expanded["scenes"][0]["at"] == "2026-09-07T18:00:00"
    scenes, _, resolved = resolve_spec(spec, tmp_path)
    assert scenes[0].thread == "devon"
    assert scenes[0].at == "2026-09-07T18:00:00-04:00"
    assert resolved["question_at"] == "2026-09-14T08:00:00-04:00"


def test_week_rejects_duplicate_chat_and_bad_human_time():
    base = {"week_of": "2026-09-07", "days": {day: [] for day in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}}
    row = {"channel": "imessage", "chat": "Devon", "time": "7 p.m.",
           "scene": "Pizza plan", "facts": ["Pizza Friday."]}
    base["days"]["Monday"] = [row, dict(row)]
    with pytest.raises(ValueError, match="repeats"):
        expand_week_spec(base)
    base["days"]["Monday"] = [dict(row, time="19:00")]
    with pytest.raises(ValueError, match="7 a.m."):
        expand_week_spec(base)


def test_local_wall_time_stays_seven_pm_across_dst(tmp_path):
    spec = {"timezone": "America/New_York", "test_scene_id": "second",
            "scenes": [
                {"id": "first", "thread": "pizza", "at": "2026-10-31T19:00:00",
                 "scene": "Devon and Me make a plan."},
                {"id": "second", "thread": "pizza", "at": "2026-11-02T19:00:00",
                 "scene": "They update it.", "facts": ["The plan moved."]}]}
    scenes, _, resolved = resolve_spec(spec, tmp_path)
    assert scenes[0].at == "2026-10-31T19:00:00-04:00"
    assert scenes[1].at == "2026-11-02T19:00:00-05:00"
    assert datetime.fromisoformat(scenes[0].at).hour == 19
    assert datetime.fromisoformat(scenes[1].at).hour == 19
    assert resolved["question_at"] == "2026-11-03T09:00:00-05:00"
    assert spread_timestamps(scenes[0], 10)[-1] == scenes[0].window_end


def test_same_thread_writer_sees_prior_messages_without_recap_instruction(tmp_path):
    scenes, _, _ = resolve_spec({"test_scene_id": "second", "scenes": [
        {"id": "first", "thread": "pizza", "scene": "Make a pizza plan."},
        {"id": "second", "thread": "pizza", "scene": "Move it later.",
         "facts": ["They agree on a later time."]}]}, tmp_path)
    prompt = scene_prompt(scenes[1], (("2026-09-09T18:00:00-04:00",
                                       "Devon", "See you at Luigi's"),))
    assert "2026-09-09 Devon: See you at Luigi's" in prompt
    assert "do not recap settled details" in prompt
    assert "don't reply as if an old message just arrived" in prompt


def test_scene_order_and_same_thread_overlap_rejected(tmp_path):
    first = {"id": "first", "thread": "pizza", "at": "2026-09-10T18:00:00",
             "scene": "First message."}
    second = {"id": "second", "thread": "pizza", "at": "2026-09-10T17:00:00",
              "scene": "Second message.", "facts": ["A fact."]}
    with pytest.raises(ValueError, match="fictional time order"):
        resolve_spec({"test_scene_id": "second", "scenes": [first, second]}, tmp_path)
    second["at"] = "2026-09-10T18:10:00"
    with pytest.raises(ValueError, match="overlapping windows"):
        resolve_spec({"test_scene_id": "second", "scenes": [first, second]}, tmp_path)


def test_nonexistent_local_clock_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-03-08T02:30:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="nonexistent local time"):
        resolve_spec(spec, tmp_path)


def test_conflicting_explicit_offset_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-09-10T19:00:00-05:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="offset conflicts"):
        resolve_spec(spec, tmp_path)


def test_ambiguous_local_clock_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-11-01T01:30:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="ambiguous local time"):
        resolve_spec(spec, tmp_path)
    spec["scenes"][0]["at"] = "2026-11-01T01:30:00-05:00"
    scenes, _, _ = resolve_spec(spec, tmp_path)
    assert scenes[0].at == "2026-11-01T01:30:00-05:00"
