"""Offline tests for the explicit native Hermes Codex configuration seam."""

import pytest
import yaml

from pacbench.harness import provision_home


def test_codex_home_is_explicit_and_does_not_copy_auth(tmp_path):
    home = tmp_path / "hermes-home"
    record = provision_home(home, provider_plugin_src=None, provider_name="memcal",
                            model="gpt-6-luna", model_provider="openai-codex")
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["model"] == {"default": "gpt-6-luna", "provider": "openai-codex",
                               "api_mode": "codex_responses"}
    assert record["auth_copied"] is False
    assert not (home / "auth.json").exists()
    assert config["memory"]["memory_enabled"] is False
    assert "terminal" in config["agent"]["disabled_toolsets"]


@pytest.mark.parametrize("options", [
    {"model": "gpt-5.6-luna", "model_provider": "openai-codex"},
    {"model": "gpt-6-luna", "model_provider": "openai-codex", "model_base_url": "https://example.com"},
])
def test_codex_rejects_substitution_and_custom_endpoints(tmp_path, options):
    home = tmp_path / "hermes-home"
    with pytest.raises(ValueError):
        provision_home(home, provider_plugin_src=None, provider_name="memcal", **options)
    assert not home.exists()


def test_codex_never_erases_existing_home(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    marker = home / "auth.json"
    marker.write_text("existing login placeholder")
    with pytest.raises(ValueError, match="existing Codex Hermes home"):
        provision_home(home, provider_plugin_src=None, provider_name="memcal",
                       model="gpt-6-luna", model_provider="openai-codex")
    assert marker.read_text() == "existing login placeholder"
