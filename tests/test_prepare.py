"""Offline authoring integrity; these tests do not measure provider accuracy."""
import json
from datetime import datetime
import pytest
from pacbench.prepare import resolve_spec, prepare
from pacbench.harness import _summarize_tool_result
from pacbench.authoring import ScenePlan, judge_prompt, scene_prompt, spread_timestamps

def test_writer_and_verifier_see_the_local_message_window():
    plan = ScenePlan(scene="Make an evening plan.",
                     at="2026-09-08T19:00:00", id="evening")
    assert "between 19:00 and 19:20 local time" in scene_prompt(plan)
    assert "between 19:00 and 19:20 local time" in judge_prompt(
        plan, [("Me", "Today is dragging")])

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
        prepare(spec, root)
    status = json.loads((root / "experiment-status.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    assert status["state"] == "incomplete"
    assert status["stage"] == "spec validation"
    assert summary["failed_stage"] == "spec validation"
    assert json.loads((root / "spec.json").read_text()) == spec

def test_failed_preparation_audit_marks_body_incomplete(tmp_path, monkeypatch):
    import importlib
    experiment = importlib.import_module("pacbench.prepare")

    root = tmp_path / "audit-failed"
    monkeypatch.setattr(experiment, "_prepare_body",
                        lambda spec, path, progress: {"state": "prepared", "results": []})
    monkeypatch.setattr(experiment, "audit_prepared",
                        lambda path: {"state": "fail", "errors": ["duplicate input"]})
    summary = experiment.prepare({"id": "pizza"}, root)
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
    assert scenes[0].window_end == "2026-10-01T17:20:00-04:00"
    assert resolved["question_at"] == "2026-10-02T09:00:00-04:00"
    assert "target_lines" not in resolved["scenes"][0]
    assert "id" not in spec["scenes"][0]

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
