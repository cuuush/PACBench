import copy
import json

import pytest

from pacbench.campaigns import pizza_day_review as control
from pacbench.openrouter_route import with_route
from pacbench.campaigns.pizza_day import QUESTION, QUESTION_AT
from pacbench.semantic_grade import Claim, judge_prompt
from pacbench.workflows.runner import judge_options
from pacbench.workflows.spec import Turn


@pytest.mark.parametrize('context, admitted', [
    ('', True),
    ('\n\n<memory-context>\nA saved memory.\n</memory-context>', True),
    ('\n\n<memory-context>unclosed', False),
    ('\n\n<memory-context>\nA saved memory.\n</memory-context>\nextra text', False),
    ('\n\n<memory-context>\nFirst.\n</memory-context>\n<memory-context>\nSecond.\n</memory-context>', False),
])
def test_native_day_admission_requires_one_complete_context_block(tmp_path, monkeypatch, context, admitted):
    from pacbench.campaigns import pizza_day as lineup
    from pacbench.workflows import progress as workflow_progress
    from pacbench.workflows.tracks import HERMES_TOOLS

    root = tmp_path/'mem0'
    scope = {'root': str(root), 'run_id': 'run', 'run_key': 'key', 'projects': lineup.projects(root),
             'controller': lineup.process_identity()}

    class Progress:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def binding(self, name):
            return {'workflow_scope': {}, 'preflight': {'ok': True},
                    'gateway-process': lineup.process_identity()}[name]

        def last_cutoff(self):
            return lineup.AT

        def active_effect(self, phase, *_):
            return phase == 'answer'

    monkeypatch.setattr(workflow_progress, 'WorkflowProgress', Progress)
    monkeypatch.setenv('PACBENCH_WORKFLOW_SCOPE', '{}')
    project = scope['projects']['mem0']['driver']
    body = with_route({'messages': [{'role': 'user', 'content': QUESTION + context}],
                       'tools': [{'function': {'name': name}} for name in HERMES_TOOLS['mem0']]},
                      profile='luna-bedrock-global')
    if admitted:
        assert lineup.admit_lineup(scope, project, body)['slot']['kind'] == 'answer'
    else:
        with pytest.raises(ValueError, match='question or tools differ'):
            lineup.admit_lineup(scope, project, body)


def test_day_judge_send_rechecks_scope_and_live_claim(tmp_path, monkeypatch):
    from pacbench import live_dispatch
    from pacbench.workflows import capability as cap
    from pacbench.workflows import grading as workflow_grading
    from pacbench.campaigns.pizza_day import LINEUP_ADMISSION

    scope = {'admission': LINEUP_ADMISSION, 'root': str(tmp_path/'honcho'), 'provider': 'honcho'}
    bound = {'sha256': 'scope'}
    effect = {'attempt_id': 'judge-attempt'}
    row = {'workflow_root': scope['root'], 'workflow_capability': bound,
           'project': 'test-judge', 'dispatch_tracking_condition': live_dispatch.CONDITION,
           'dispatch_context': {'attempt_id': 'wire-attempt'},
           'dispatch_owner': live_dispatch.process_identity(), 'dispatch_execution': 'prepared',
           'dispatch_events': [], 'workflow_slot': {'kind': 'judge', 'provider': 'honcho',
                                                    'checkpoint_effect': effect}}
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    monkeypatch.setattr(cap, 'load', lambda: scope)
    active = False
    monkeypatch.setattr(workflow_grading, 'active_claim',
                        lambda c, project, claim: active and (c, project, claim) == (scope, 'test-judge', effect))
    with pytest.raises(ValueError, match='no longer live'):
        live_dispatch.mark_possible(row, 'offline-test')
    assert row['dispatch_execution'] == 'prepared' and not row['dispatch_events']
    active = True
    with pytest.raises(ValueError, match='prepared scope'):
        live_dispatch.mark_possible({**row, 'workflow_capability': {}}, 'offline-test')
    live_dispatch.mark_possible(row, 'offline-test')
    assert row['dispatch_execution'] == 'ambiguous'


def test_day_receipt_summary_reconciles_a_settled_attempt(tmp_path):
    from pacbench.canary_capability import binding, sha
    from pacbench.live_dispatch import operation_id
    from pacbench.response_receipts import PROTOCOL
    from pacbench.workflows.recording import receipt_summary

    root = tmp_path/'honcho'
    receipts = root/'billing-receipts'
    receipts.mkdir(parents=True)
    scope = root/'workflow-scope.json'
    scope.write_text(json.dumps({'root': str(root), 'run_id': 'day',
                                 'projects': {'honcho': {'judge': 'honcho-judge'}}}))
    receipt = receipts/'attempt.json'
    receipt.write_text(json.dumps({'protocol': PROTOCOL, 'attempt_id': 'attempt',
                                   'generation_id': 'generation', 'usage': {'cost': '0.0001'}}))
    row = {'id': 'attempt', 'project': 'honcho-judge', 'request_sha256': 'request',
           'workflow_root': str(root), 'workflow_capability': binding(scope),
           'state': 'settled', 'upstream_request_id': 'generation', 'settled_charge_usd': '0.0001',
           'billing_receipt': {'relative_path': 'billing-receipts/attempt.json', 'sha256': sha(receipt)}}
    row['dispatch_context'] = {'run_id': 'day', 'attempt_id': 'attempt', 'arm_id': 'honcho',
                               'phase': 'judging', 'operation_id': operation_id(row)}
    attempts = {}
    unknown = {**row, 'id':'retry', 'retry_of':'attempt','state':'pending'}
    unknown['dispatch_context'] = {**row['dispatch_context'], 'attempt_id':'retry'}
    summary = receipt_summary(root, [row, unknown], 'day', attempts=attempts)
    assert summary['receipted_usd'] == attempts['attempt']['actual_usd'] == '0.0001'
    assert summary['complete_cost_usd'] is None and attempts['retry']['actual_usd'] is None
    assert attempts['retry']['retry_of'] == 'attempt' and summary['retried_attempts'] == 1


def test_control_admits_only_exact_wrong_answer_judge_request(monkeypatch):
    claims=(Claim('fact-1', 'The companion is Devon.', ('message-a',)),)
    turn=Turn('pizza-plan',QUESTION_AT,QUESTION,'pizza-recap','initial',claims)
    surface=dict(answer=control.ANSWER,history=[dict(role='user',content=QUESTION)],conversation_times=[])
    monkeypatch.setattr(control,'control_input',lambda _: (turn,surface))
    monkeypatch.setenv('PACBENCH_WORKFLOW_SCOPE','{}')
    scope=dict(preparation='prepared',root='/campaign/judge-control',run_id='campaign')
    prompt=judge_prompt(QUESTION,control.ANSWER,claims,**judge_options(turn,surface))
    wire=with_route(dict(messages=[dict(role='user',content=prompt)],stream=True,stream_options={'include_usage':True},
                        max_completion_tokens=32768,response_format={'type':'json_object'}),profile='luna-bedrock-global')
    assert control.admit(scope,control.PROJECT,wire)['slot']['kind']=='judge'
    different=copy.deepcopy(wire)
    different['messages'][0]['content']=prompt.replace(control.ANSWER,'Devon is your companion.')
    for project,body in [('unrelated-driver',wire),(control.PROJECT,different),(control.PROJECT,{**wire,'tools':[]})]:
        with pytest.raises(ValueError,match='unexpected calibration request'):
            control.admit(scope,project,body)


@pytest.mark.parametrize('previous_judge', [True, False])
def test_unsent_judge_requires_absence_of_prepared_transport(tmp_path, previous_judge):
    import json
    from types import SimpleNamespace
    from pacbench.campaigns.pizza_day_retained import unsent_judge
    from pacbench.campaigns.pizza_day import LINEUP_ADMISSION
    from pacbench.canary_capability import digest

    root = tmp_path/'honcho'
    directory = root/'workflow'
    calls = directory/'model-calls'
    calls.mkdir(parents=True)
    staging = directory/'judgments/pizza-plan/judgment-attempts'
    staging.mkdir(parents=True)
    turn = Turn('pizza-plan', QUESTION_AT, QUESTION, 'pizza-recap', 'initial',
                (Claim('fact-1', 'Devon is the companion.', ('message-a',)),))
    surface = dict(answer='With Devon.', history=[dict(role='user', content=QUESTION)], conversation_times=[])
    prompt = judge_prompt(QUESTION, surface['answer'], turn.claims, **judge_options(turn, surface))
    call = dict(status='failed', error='OperationalError: disk I/O error',
                request={'messages':[{'role':'user','content':prompt}]})
    (calls/'failed.json').write_text(json.dumps(call))
    claim = {'attempt_id':'original'}
    bindings = {'judge-intent/pizza-plan/'+digest(prompt):claim}
    progress = SimpleNamespace(directory=directory, execution_sealed=True, grade_states=lambda: {'claimed':1},
                               get_claimed_grade=lambda _: (turn,surface), active_grade=lambda _: claim,
                               binding=bindings.get, bind=bindings.__setitem__)
    scope = dict(provider='honcho',root=str(root),admission=LINEUP_ADMISSION,projects={'honcho':{'judge':'test-judge'}})
    rows = [dict(id='original-answer',state='settled',accounting_role='hermes', project='test-driver')]
    if previous_judge:
        rows.append(dict(id='possible-judge', state='receipt_pending', accounting_role='judge', project='test-judge'))
        with pytest.raises(ValueError, match='cannot be ruled out'):
            unsent_judge(progress, scope, SimpleNamespace(snapshot=lambda:rows))
        assert staging.exists()
    else:
        assert unsent_judge(progress,scope,SimpleNamespace(snapshot=lambda:rows)) == 'pizza-plan'
        assert (directory/'unsent-judge-staging-original/judgment-attempts').is_dir()
        assert bindings['judge-unsent-local-failure/pizza-plan']['claim'] == claim
