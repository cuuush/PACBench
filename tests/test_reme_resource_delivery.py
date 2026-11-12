import hashlib
import json
from unittest.mock import patch

import pytest

from pacbench.tool_policy import policy_record
from provider_adapters.reme_resource import ingest


def fixture(tmp_path):
    source, root = tmp_path / "source", tmp_path / "reme"
    source.mkdir()
    root.mkdir()
    workspace = root / "reme-workspace"
    workspace.mkdir()
    receipts = root / "reme-native-resource-results"
    receipts.mkdir()
    text = "[2026-09-10T18:00:00-04:00] Pat\nOriginal unmodified thread.\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    (source / "messages.txt").write_text(text)
    (source / "source.json").write_text(json.dumps({"document_sha256": digest,
        "source_ids": ["m1"], "at": "2026-09-10T18:00:00-04:00"}))
    (root / "reme-resource-config.json").write_text(json.dumps({"workspace": str(workspace), "receipts": str(receipts)}))
    relative = f"resource/2026-09-10/source-{digest}.txt"
    return source, root, workspace, receipts, relative, text


def test_resource_delivery_requires_native_completion_and_index_witness(tmp_path):
    source, root, workspace, receipts, relative, text = fixture(tmp_path)

    def native_completion(_):
        assert (workspace / relative).read_text() == text
        assert not list((workspace / "daily").glob("*.md"))
        card = workspace / "daily/2026-09-10/native-card.md"
        card.parent.mkdir(parents=True)
        card.write_text(f"---\nsource_resource: '[[{relative}]]'\n---\nNative generated card.\n")
        receipt = {"response": {"success": True, "metadata": {"results": [
            {"path": relative, "success": True, "metadata": {"path": str(card.relative_to(workspace)),
             "source_resource": f"[[{relative}]]"}}]}}, "hook_metadata": {"index_observation_complete": True}}
        (receipts / "new.json").write_text(json.dumps(receipt))

    with patch("provider_adapters.reme_resource.time.sleep", side_effect=native_completion):
        result = ingest(source, root, timeout=1)
    assert result["ready"] is True
    assert result["manual_job_invocations"] == 0
    assert result["source_readback_exact"] is True
    with pytest.raises(ValueError, match="resubmitted"):
        ingest(source, root)


def test_resource_delivery_rejects_changed_frozen_source(tmp_path):
    source, root, workspace, *_ = fixture(tmp_path)
    (source / "messages.txt").write_text("changed")
    with pytest.raises(ValueError, match="frozen manifest"):
        ingest(source, root)
    assert not list(workspace.rglob("*.txt"))


def test_empty_tools_are_only_allowed_for_verified_prefetch_only_provider():
    policy = policy_record("reme", [])
    assert policy["allowed_tools"] == []
    assert policy["native_mode"] == "automatic-prefetch-only"
    for provider in ("mem0", "honcho", "unknown"):
        with pytest.raises(ValueError):
            policy_record(provider, [])
