"""Harness regressions; these do not claim model or hosted-provider accuracy."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pacbench.native_pilot import provision, read_prepared, run
from pacbench.authoring import AuthoringError, ScenePlan, scene_prompt
from pacbench.prepare import main, resolve_spec

ROOT = Path(__file__).resolve().parents[1]


def base():
    return json.loads((ROOT / "specs/pizza_day.json").read_text())


def test_writer_has_size_constraint_not_oracle_padding():
    plan = ScenePlan(scene="A normal chat", message_range=[18, 22])
    assert "between 18 and 22 messages" in scene_prompt(plan)
    for bounds in ([1, 10], [22, 18], [18, 30001], [True, 10]):
        with pytest.raises(AuthoringError):
            ScenePlan(scene="chat", message_range=bounds)


def test_mem0_uses_distinct_native_profile_identities(tmp_path):
    with patch("pacbench.native_pilot.client_settings", return_value=('http://127.0.0.1:8099/p/unit/v1','offline-test')), \
         patch("pacbench.gateway_client.client_settings", return_value=('http://127.0.0.1:8099/p/unit/v1','offline-test')), \
         patch("pacbench.native_pilot.harness.provision_home") as provision_home:
        def make_home(home, **kwargs):
            home.mkdir(parents=True)
        provision_home.side_effect = make_home
        first = tmp_path / "first"
        second = tmp_path / "second"
        env = provision(first, "mem0")
        provision(second, "mem0")
    a = json.loads((first / "hermes-home/mem0.json").read_text())
    b = json.loads((second / "hermes-home/mem0.json").read_text())
    assert a["user_id"] != b["user_id"]
    assert a["mode"] == b["mode"] == "oss"
    assert a["oss"]["vector_store"]["provider"] == "qdrant"
    assert a["oss"]["embedder"]["provider"] == "ollama"
    assert a["oss"]["llm"]["config"]["model"].endswith("-contributor")
    assert "max_tokens" not in a["oss"]["llm"]["config"]
    assert a['oss']['llm']['config']['is_reasoning_model'] is True
    assert 'reasoning_effort' not in a['oss']['llm']['config']
    assert env["HOME"] == str(first / "fallback-home")
    assert env["PACBENCH_NO_BUILTIN_MEMORY"] == "1"
    assert env["PACBENCH_PROVIDER_ONLY"] == "1"


def test_mem0_native_reasoning_parameters_do_not_inherit_legacy_chat_limit(tmp_path):
    import os
    import subprocess
    from pacbench.harness import ISOLATED_BIN
    python=ISOLATED_BIN.parent/'python'
    if not python.exists():
        pytest.skip('Native Hermes interpreter is not installed')
    installed=subprocess.run([str(python),'-c','import importlib.util; raise SystemExit(importlib.util.find_spec("mem0") is None)'],
        capture_output=True,text=True,timeout=15)
    if installed.returncode:
        pytest.skip('Native Mem0 SDK is not installed; no simulated SDK pass')
    script = '''
from mem0.configs.llms.openai import OpenAIConfig
from mem0.llms.openai import OpenAILLM
cfg=OpenAIConfig(model='muse-spark-1.3-contributor',api_key='offline-test',is_reasoning_model=True)
assert cfg.max_tokens==2000 and cfg.reasoning_effort is None
params=OpenAILLM(cfg)._get_supported_params(messages=[{'role':'user','content':'offline probe'}])
assert not {'max_tokens','max_completion_tokens','temperature','reasoning_effort'} & params.keys()
'''
    result=subprocess.run([str(ISOLATED_BIN.parent/'python'),'-c',script],
        env={**os.environ,'HOME':str(tmp_path),'MEM0_TELEMETRY':'false'},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_live_budget_block_happens_before_any_model_call(tmp_path):
    with patch("pacbench.native_pilot.read_prepared", return_value=([], None, {})), \
         patch("pacbench.native_pilot.require_live_cost_boundary", side_effect=RuntimeError("cannot enforce $10 total spend")), \
         patch("pacbench.native_pilot.load_driver") as driver, \
         patch("pacbench.native_pilot.harness.run_turn") as turn:
        with pytest.raises(RuntimeError, match="10 total spend"):
            run(tmp_path, tmp_path / "out")
        driver.assert_not_called()
        turn.assert_not_called()
    assert json.loads((tmp_path / "out/summary.json").read_text())["state"] == "blocked"


def test_cli_audit_failure_cannot_be_success(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("{}")
    with patch("sys.argv", ["runner", str(path), "--output", str(tmp_path / "out")]), \
         patch("pacbench.prepare.prepare", return_value={
             "state": "incomplete", "results": [{"state": "valid"}]}):
        assert main() == 1


def test_provider_duplicates_rejected_before_paid_generation():
    spec = base()
    spec["providers"] = ["memcal", "memcal"]
    with pytest.raises(ValueError, match="select providers"):
        resolve_spec(spec, Path("scratch"))


@pytest.mark.parametrize("provider", ["holographic", "memcal"])
def test_real_local_native_read_only_seam(tmp_path, provider):
    from pacbench.native_pilot import MEMCAL_SRC, probe
    from pacbench.harness import ISOLATED_BIN
    if not ISOLATED_BIN.exists() or not MEMCAL_SRC.exists():
        pytest.skip("native Hermes or Memcal checkout unavailable; no simulated pass")
    from pacbench.gateway_client import STATE
    if not (STATE/'client-token').is_file() or not (STATE/'address.json').is_file():
        pytest.skip('Read-only integration requires the configured gateway; no replacement ledger or credentials')
    root = tmp_path / provider
    env = provision(root, provider, local_timezone="America/New_York")
    assert env["TZ"] == "America/New_York"
    capability = probe(root, provider, env, smoke=True)
    assert capability["available"] is True
    output = json.loads(capability["read_only_smoke"]["output"])
    assert "error" not in output
    assert capability["live_verified"] is False


def test_managed_model_config_and_explicit_file_precedence(tmp_path):
    from pacbench.generation import load_config
    path = tmp_path / "owned.env"
    with patch.dict("os.environ", {"MODEL": "runtime-model", "META": "runtime-key"}, clear=True):
        config, key = load_config(path)
        assert (config.model, key) == ("runtime-model", "runtime-key")
        path.write_text("MODEL=file-model\nMETA=file-key\n")
        config, key = load_config(path)
        assert (config.model, key) == ("file-model", "file-key")


@pytest.mark.parametrize("smoke", [
    {}, {"error": "timeout"}, {"output": "bad JSON"},
    {"output": '{"error":"unavailable"}'}, {"output": '{"success":false}'},
])
def test_native_smoke_error_cannot_be_a_wiring_pass(smoke):
    from pacbench.native_pilot import smoke_succeeded
    assert smoke_succeeded({"read_only_smoke": smoke}) is False


def test_codex_provision_routes_hermes_separately_from_provider(tmp_path):
    from unittest.mock import patch
    from pacbench.native_pilot import provision
    root = tmp_path / 'arm'
    root.mkdir()
    with patch('pacbench.native_pilot.client_settings', return_value=('http://127.0.0.1:8099/p/test/v1', 'test-key')), \
         patch('pacbench.native_pilot.harness.provision_home') as home:
        env = provision(root, 'holographic', 'muse-spark-1.3-contributor', inference_route='codex-subscription')
    assert home.call_args.kwargs['model'] == 'gpt-6-luna'
    assert home.call_args.kwargs['model_provider'] == 'openai-codex'
    assert home.call_args.kwargs['model_base_url'] is None
    assert env['PACBENCH_INFERENCE_ROUTE'] == 'codex-subscription'
    assert set(json.loads(env['PACBENCH_USAGE_ROLES']).values()) == {'memory_provider'}


def test_native_pilot_rejects_unknown_route_before_creating_output(tmp_path):
    from pacbench.native_pilot import run
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='Unknown inference route'):
        run(None, output, inference_route='unknown')
    assert not output.exists()


def test_codex_native_pilot_fails_before_creating_output(tmp_path):
    output = tmp_path / 'output'
    with patch('pacbench.native_pilot.provider_names', return_value=['holographic']):
        with pytest.raises(RuntimeError, match='provision/login/resume'):
            run(tmp_path / 'prepared', output, providers=['holographic'], inference_route='codex-subscription')
    assert not output.exists()
