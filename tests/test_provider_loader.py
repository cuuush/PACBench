"""Provider resolution must support catalog namespaces and older bundled homes."""
import pacbench.providers.provision as provider_setup
from types import SimpleNamespace

import pytest

from pacbench import providers as provider_loader


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


def test_catalog_moved_providers_selectable_and_unknown_migration_blocked(monkeypatch):
    from pacbench.catalog_plugins import CATALOG
    from pacbench.workflows import native_run as native_pilot
    assert set(CATALOG) <= set(provider_setup.provider_names())
    monkeypatch.setattr(provider_setup, 'catalog_migrated_providers', lambda: set(CATALOG) | {'unreviewed'})
    with pytest.raises(RuntimeError, match="No reviewed trial-home"):
        provider_setup.require_trial_provider("unreviewed")
    for provider in CATALOG:
        provider_setup.require_trial_provider(provider, resume=True)
    with pytest.raises(ValueError, match="resume protocol"):
        provider_setup.require_trial_provider("reme", resume=True)


def test_byterover_is_not_selectable_or_provisioned(tmp_path, monkeypatch):
    from pacbench.workflows import native_run as native_pilot
    assert "byterover" not in provider_setup.provider_names()
    def no_configuration(*args, **kwargs):
        pytest.fail("ByteRover must be rejected before model configuration")
    monkeypatch.setattr(native_pilot, "client_settings", no_configuration)
    with pytest.raises(ValueError, match="removed from the active benchmark"):
        provider_setup.provision(tmp_path / "arm", "byterover")
    assert not (tmp_path / "arm").exists()
