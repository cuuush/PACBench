"""Provider resolution must support catalog namespaces and older bundled homes."""
from types import SimpleNamespace

import pytest

from pacbench import provider_loader


def test_catalog_resolver_receives_provider_and_submodule(monkeypatch):
    calls = []
    expected = object()
    memory = SimpleNamespace(import_provider_module=lambda *args: calls.append(args) or expected)
    monkeypatch.setattr(provider_loader, "import_module", lambda name: memory)
    assert provider_loader.provider_module("honcho", "client") is expected
    assert calls == [("honcho", "client")]


def test_older_bundled_provider_fallback(monkeypatch):
    calls = []
    expected = object()
    def load(name):
        calls.append(name)
        return SimpleNamespace() if name == "plugins.memory" else expected
    monkeypatch.setattr(provider_loader, "import_module", load)
    assert provider_loader.provider_module("mem0", "_setup") is expected
    assert calls == ["plugins.memory", "plugins.memory.mem0._setup"]


def test_catalog_load_errors_are_not_masked_as_legacy_imports(monkeypatch):
    def fail(*args):
        raise ImportError("missing catalog dependency")
    monkeypatch.setattr(provider_loader, "import_module", lambda name: SimpleNamespace(import_provider_module=fail))
    with pytest.raises(ImportError, match="missing catalog dependency"):
        provider_loader.provider_module("supermemory")


def test_catalog_moved_providers_are_selectable_but_fresh_home_fails_closed(tmp_path, monkeypatch):
    from pacbench import native_pilot
    monkeypatch.setattr("pacbench.live_trials.require_live_trials_ready", lambda: None)
    names = native_pilot.provider_names()
    assert {"mem0", "honcho", "hindsight", "supermemory", "openviking"} <= set(names)
    def no_gateway(*args, **kwargs):
        pytest.fail("gateway must not be touched before catalog preflight")
    monkeypatch.setattr(native_pilot, "client_settings", no_gateway)
    for name in ("mem0", "honcho", "hindsight", "supermemory", "openviking"):
        with pytest.raises(RuntimeError, match="fresh trial homes.*pinned plugin"):
            native_pilot.provision(tmp_path / name, name)
        assert not (tmp_path / name).exists()
