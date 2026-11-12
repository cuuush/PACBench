"""Saved native prompt and project-discovery isolation; no provider accuracy mocks."""
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pacbench import harness
from pacbench.context_isolation import actual_context, workspace_for


def native_prompt(home, cwd, prompt):
    home.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE system_prompts (hash TEXT, prompt TEXT)')
        db.execute('CREATE TABLE sessions (id TEXT, cwd TEXT, system_prompt TEXT, system_prompt_hash TEXT)')
        db.execute('INSERT INTO system_prompts VALUES (?,?)', (digest, prompt))
        db.execute('INSERT INTO sessions VALUES (?,?,NULL,?)', ('native-session', str(cwd), digest))


def test_actual_prompt_rejects_project_context_even_with_clean_cwd(tmp_path):
    home = tmp_path / 'arm/hermes-home'
    workspace = workspace_for(home)
    native_prompt(home, workspace, 'Native agent\n# Project Context\nPrivate benchmark notes')
    result = actual_context(home, 'native-session', workspace)
    assert result['state'] == 'fail' and 'project context' in result['reason']


def test_actual_prompt_rejects_native_cwd_mismatch(tmp_path):
    home = tmp_path / 'arm/hermes-home'
    workspace = workspace_for(home)
    native_prompt(home, harness.REPO, 'Native provider instructions')
    assert actual_context(home, 'native-session', workspace)['state'] == 'fail'


def test_actual_prompt_requires_native_evidence(tmp_path):
    home = tmp_path / 'arm/hermes-home'
    workspace = workspace_for(home)
    assert actual_context(home, 'native-session', workspace)['state'] == 'fail'
    native_prompt(home, workspace, 'Native provider instructions')
    assert actual_context(home, 'native-session', workspace)['state'] == 'pass'
    assert actual_context(home, 'different-session', workspace)['state'] == 'fail'
    assert workspace_for(home) == workspace


def test_shared_session_can_audit_its_original_native_prompt_revision(tmp_path):
    home = tmp_path / 'arm/hermes-home'; workspace = workspace_for(home)
    native_prompt(home, workspace, 'Initial native provider context')
    initial = actual_context(home, 'native-session', workspace)
    changed = 'Updated native provider context'
    digest = hashlib.sha256(changed.encode()).hexdigest()
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('INSERT INTO system_prompts VALUES (?,?)', (digest, changed))
        db.execute('UPDATE sessions SET system_prompt_hash=?', (digest,))
    assert actual_context(home, 'native-session', workspace,
                          prompt_hash=initial['system_prompt_sha256']) == initial
    assert actual_context(home, 'native-session', workspace)['system_prompt_sha256'] == digest
    assert actual_context(home, 'native-session', workspace, prompt_hash='invented')['state'] == 'fail'


def test_recall_launches_outside_benchmark_without_disabling_memory_hooks(tmp_path):
    home = tmp_path / 'arm/hermes-home'; home.mkdir(parents=True)
    def run(args, **kwargs):
        workspace = Path(kwargs['cwd'])
        assert not workspace.is_relative_to(harness.REPO)
        assert kwargs['env']['TERMINAL_CWD'] == str(workspace)
        assert '--ignore-rules' not in args
        assert args[args.index('-q') + 1] == 'Recall my plan'
        assert kwargs['env']['PACBENCH_NOW'] == '2026-09-11T09:00:00-04:00'
        native_prompt(home, workspace, 'Native provider instructions')
        event = {'type': 'system', 'subtype': 'init', 'session_id': 'native-session'}
        return subprocess.CompletedProcess(args, 0, json.dumps(event), '')
    with patch('pacbench.harness.subprocess.run', side_effect=run):
        turn = harness.run_turn('Recall my plan', at='2026-09-11T09:00:00-04:00',
                                home=home, allowlist='mem0_search')
    assert turn.context_isolation['state'] == 'pass'


def test_native_context_discovery_sees_no_benchmark_files(tmp_path):
    python = harness.ISOLATED_BIN.parent / 'python'
    if not python.exists():pytest.skip('Native Hermes unavailable')
    home = tmp_path / 'arm/hermes-home'; home.mkdir(parents=True)
    workspace = workspace_for(home)
    code = ('from agent.prompt_builder import build_context_files_prompt; '
            'import json,sys; print(json.dumps(build_context_files_prompt(cwd=sys.argv[1], '
            'skip_soul=True,home_override=__import__("pathlib").Path(sys.argv[2]))))')
    proc = subprocess.run([str(python), '-c', code, str(workspace), str(home)],
        cwd=harness.ISOLATED_TREE, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ''


def test_turn_env_discards_inherited_and_explicit_prompt_overrides(tmp_path, monkeypatch):
    for key in ('HERMES_EPHEMERAL_SYSTEM_PROMPT', 'HERMES_ENVIRONMENT_HINT',
                'HERMES_IGNORE_RULES', 'HERMES_DESKTOP_TERMINAL'):
        monkeypatch.setenv(key, 'inherited override')
    env = harness.turn_env('2026-09-11T09:00:00-04:00', tmp_path, '',
                           {'HERMES_EPHEMERAL_SYSTEM_PROMPT': 'explicit override'})
    assert all(key not in env for key in ('HERMES_EPHEMERAL_SYSTEM_PROMPT',
        'HERMES_ENVIRONMENT_HINT', 'HERMES_IGNORE_RULES', 'HERMES_DESKTOP_TERMINAL'))


@pytest.mark.parametrize('protocol',['user-clock-prefix-v1','ephemeral-system-clock-v2','unknown'])
def test_historical_instruction_conditions_cannot_execute_again(tmp_path,protocol):
    home=tmp_path/'hermes-home';home.mkdir()
    with patch('pacbench.harness.subprocess.run') as launch:
        with pytest.raises(ValueError,match='New execution requires native-question-v2'):
            harness.run_turn('Recall my plan',at='2026-09-11T09:00:00-04:00',home=home,
                allowlist='mem0_search',extra_env={'PACBENCH_CLOCK_CONTEXT_PROTOCOL':protocol})
    launch.assert_not_called()
