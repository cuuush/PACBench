import json
from types import SimpleNamespace

import pytest

from pacbench.semantic_experiment import resolve_spec, run_experiment, tool_behavior
from pacbench.harness import _summarize_tool_result


def test_provider_tool_use_is_distinct_from_capability():
    memcal = SimpleNamespace(tool_calls=[{"name": "memcal_search_archive"}],
                             tool_results=[{"success": True, "text": "found"}], policy_blocks=0)
    hindsight = SimpleNamespace(tool_calls=[{"name": "tool_search"}],
                                tool_results=[{"success": True}], policy_blocks=0)
    assert tool_behavior("memcal", memcal) == {
        "provider_tool_attempts": ["memcal_search_archive"],
        "provider_tool_calls": ["memcal_search_archive"],
        "blocked_provider_calls": [],
        "retrieval_called": True, "policy_blocks": 0}
    assert tool_behavior("hindsight", hindsight)["retrieval_called"] is False


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


def test_blocked_tool_result_is_not_successful():
    event = {"type": "tool_result", "tool": "mcp__hindsight__list_memories",
             "success": True,
             "result": {"error": "PACBENCH-BLOCKED: outside memory-tool boundary"}}
    result = _summarize_tool_result(event)
    assert result["success"] is False
    assert result["policy_blocked"] is True


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
