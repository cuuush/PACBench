"""Frozen Pizza Day bundles are content-addressed and fail on tampering."""

import json
from pathlib import Path

import pytest

from pacbench.frozen import bundle_hash, load_bundle, messages_for, write_bundle


def test_bundle_round_trip_and_tamper(tmp_path: Path):
    bundle = {"bundle_version": "0.2.0", "lives": {"pizza-day": {
        "observations": [{"source_id": "pizza/1", "text": "Friday at 7pm"}]}}}
    path = tmp_path / "bundle.json"
    pinned = write_bundle(bundle, path)
    assert pinned == bundle_hash(bundle)
    assert load_bundle(path, pinned)["lives"]["pizza-day"]["observations"][0]["text"] == "Friday at 7pm"

    edited = json.loads(path.read_text())
    edited["lives"]["pizza-day"]["observations"][0]["text"] = "Saturday at 8pm"
    path.write_text(json.dumps(edited))
    with pytest.raises(RuntimeError, match="hash mismatch"):
        load_bundle(path, pinned)
    with pytest.raises(RuntimeError, match="missing"):
        load_bundle(tmp_path / "missing.json", pinned)


def test_new_messages_key_and_historical_bundle_read_without_rewrite(tmp_path: Path):
    for key in ("messages", "observations"):
        bundle = {"lives": {"pizza-day": {key: [{"source_id": "pizza/1"}]}}}
        path = tmp_path / f"{key}.json"
        pinned = write_bundle(bundle, path)
        loaded = load_bundle(path, pinned)
        assert messages_for(loaded, "pizza-day") == [{"source_id": "pizza/1"}]
        assert bundle_hash(loaded) == pinned
