import json
from types import SimpleNamespace

import pytest

from pacbench import saved_completion as sc
from pacbench.semantic_grade import Grade, GradedClaim


@pytest.fixture
def saved(tmp_path, monkeypatch):
    component = tmp_path / 'run'
    component.mkdir()
    source = component / 'original.json'
    source.write_text('{"answer":"kept"}')
    summary = {'evidence_diagnostic_protocol': 'answer-visible-evidence-v2',
               'judge_protocol': 'semantic-judge-date-scope-v2',
               'provenance_version': 'source-provenance-v3'}
    contract = SimpleNamespace(claims=[SimpleNamespace(id='fact-1', statement='A fact', evidence=())])
    turn = SimpleNamespace(answer='kept', at='2026-10-12T11:40:00-04:00', clock_context=None, prompt='question')
    monkeypatch.setattr(sc, 'prepare', lambda *a: (summary, {'id': 'last', 'session': 'fresh'},
        contract, {'plan_hash': 'frozen'}, turn, {}, {'original.json': sc._sha(source)}))
    monkeypatch.setattr(sc, 'diagnostic_inputs', lambda *a, **kw: {})
    monkeypatch.setattr(sc, 'judge_question', lambda t: t.prompt)
    return component, source, component / 'reviews' / 'completion'


def test_complete_grades_saved_answer_without_native_calls(saved, monkeypatch):
    component, source, overlay = saved
    original = source.read_bytes()
    grade = Grade('last', slots=[GradedClaim('fact-1', 'correct', 'yes')], correct=1,
                  recovery=[{'id': 'fact-1', 'kind': 'provided', 'reason': 'yes'}])
    monkeypatch.setattr(sc, 'grade_native_semantic', lambda *a, **kw: grade)
    def recovery(**kw):
        with pytest.raises(RuntimeError, match='cannot execute provider'):
            kw['run_turn']('unexpected')
        return {'score': {'retention_credit': 1}}
    monkeypatch.setattr(sc, 'run_followups', recovery)
    result = sc.run(component, 'memcal', overlay, lambda prompt: None)
    assert result['state'] == 'complete' and result['provider_calls'] == 0
    assert result['original_files_unchanged'] and source.read_bytes() == original
    assert json.loads((overlay / 'manifest.json').read_text())['source_files']['original.json'] == sc._sha(source)
    with pytest.raises(ValueError, match='new direct child'):
        sc.run(component, 'memcal', overlay, lambda prompt: None)


def test_implicit_omission_stops_before_followup_authoring(saved, monkeypatch):
    component, _, overlay = saved
    grade = Grade('last', slots=[GradedClaim('fact-1', 'missed', 'absent')], missed=1,
                  recovery=[{'id': 'fact-1', 'kind': 'implicit_omission', 'reason': 'absent'}])
    # Eligibility requires at least one initial correct claim.
    grade.correct = 1
    monkeypatch.setattr(sc, 'grade_native_semantic', lambda *a, **kw: grade)
    monkeypatch.setattr(sc, 'run_followups', lambda **kw: pytest.fail('native followup cannot run'))
    result = sc.run(component, 'memcal', overlay, lambda prompt: None)
    assert result['state'] == 'followup-required' and result['provider_calls'] == 0


def test_changed_original_is_rejected(saved, monkeypatch):
    component, source, overlay = saved
    grade = Grade('last', correct=1, recovery=[])
    def judge(*a, **kw):
        source.write_text('changed during judge')
        return grade
    monkeypatch.setattr(sc, 'grade_native_semantic', judge)
    monkeypatch.setattr(sc, 'run_followups', lambda **kw: {'score': {}})
    with pytest.raises(ValueError, match='Original run changed'):
        sc.run(component, 'memcal', overlay, lambda prompt: None)


@pytest.mark.parametrize('tool,valid',[('honcho_search',True),('terminal',False)])
def test_native_retry_observer_repair_still_enforces_actual_tools(tmp_path,monkeypatch,tool,valid):
    from pacbench import clock_context,api_input_trace,gateway_python_clock
    (tmp_path/'provider-clock.json').write_text('{}')
    (tmp_path/'tool-policy.json').write_text(json.dumps({'allowed_tools':['honcho_search'],'source_argument_rules':{}}))
    monkeypatch.setattr(clock_context,'delivery',lambda *a,**kw:{'state':'pass','native_network_continuation_api_input_ids':['retry']})
    monkeypatch.setattr(api_input_trace,'rows',lambda *a:[{'request_id':'retry','turn_nonce':'nonce'}])
    monkeypatch.setattr(gateway_python_clock,'audit_witness',lambda *a:None)
    t=SimpleNamespace(policy_blocks=1,clock_context={'state':'fail','reason':'Scenario clock or natural question differs at HTTP boundary'},
        context_isolation={'state':'pass'},hermes_gateway={'turn_nonce':'nonce'},tool_calls=[{'name':tool,'input':{}}],answer='Original answer')
    if valid:
        repaired=sc.normalize_native_continuation(tmp_path,t)
        assert repaired.policy_blocks==0 and repaired.answer=='Original answer'
    else:
        with pytest.raises(ValueError,match='Actual tool policy violation'):sc.normalize_native_continuation(tmp_path,t)
