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


def test_catalog_moved_providers_selectable_and_unknown_migration_blocked():
    from pacbench import native_pilot
    names = native_pilot.provider_names()
    assert {"mem0", "honcho", "hindsight", "supermemory", "openviking"} <= set(names)
    with pytest.raises(RuntimeError, match="No reviewed trial-home"):
        native_pilot.require_trial_provider("holographic")
    with pytest.raises(RuntimeError, match="recovery protocol"):
        native_pilot.require_trial_provider("openviking", resume=True)


def test_byterover_is_not_selectable_or_provisioned(tmp_path, monkeypatch):
    from pacbench import native_pilot
    assert "byterover" not in native_pilot.provider_names()
    def no_configuration(*args, **kwargs):
        pytest.fail("ByteRover must be rejected before model configuration")
    monkeypatch.setattr(native_pilot, "client_settings", no_configuration)
    with pytest.raises(ValueError, match="not an active trial provider"):
        native_pilot.provision(tmp_path / "arm", "byterover")
    assert not (tmp_path / "arm").exists()
