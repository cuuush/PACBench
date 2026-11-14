"""Grade a saved terminal answer without rerunning a provider or changing its run.

This deliberately narrow recovery accepts only the final frozen checkpoint,
with every preceding checkpoint complete. Its separately hashed overlay leaves
all original answers, grades, ingestion receipts and failed attempts intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import harness
from .clock_context import judge_question
from .evaluation import load_evaluations, judge_evidence
from .followups import natural_eligibility, run_followups, save
from .native_checks import diagnostic_inputs, session_key
from .native_pilot import read_prepared
from .semantic_grade import Claim, grade_native_semantic


def _read(path):
    return json.loads(path.read_text())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_native_continuation(arm, turn):
    """Fix only a demonstrated observer rejection of a pinned native retry."""
    from .clock_context import delivery,NATIVE
    from .api_input_trace import rows
    from .gateway_python_clock import audit_witness
    if (turn.policy_blocks!=1 or (turn.clock_context or {}).get('state')!='fail' or
            (turn.clock_context or {}).get('reason')!='Scenario clock or natural question differs at HTTP boundary' or
            (turn.context_isolation or {}).get('state')!='pass'):
        raise ValueError('Not the native network-continuation observer failure')
    gateway=turn.hermes_gateway or {}
    identifiers=[r['request_id'] for r in rows(arm/'api-inputs.jsonl')
                 if gateway.get('turn_nonce') and r.get('turn_nonce')==gateway['turn_nonce']]
    observed=delivery(arm,turn,identifiers=identifiers,protocol=NATIVE)
    if observed['state']!='pass' or not observed.get('native_network_continuation_api_input_ids'):
        raise ValueError('Pinned native continuation and original actor inputs are not independently verified')
    audit_witness(arm,vars(turn),_read(arm/'provider-clock.json'))
    policy=_read(arm/'tool-policy.json')
    turn.policy_blocks=0
    harness.enforce_provider_tools(turn,{'PACBENCH_PROVIDER_ONLY':'1',
        'PACBENCH_TOOL_ALLOWLIST':','.join(policy['allowed_tools']),
        'PACBENCH_SOURCE_ARGUMENT_RULES':json.dumps(policy['source_argument_rules'])})
    if turn.policy_blocks:raise ValueError('Actual tool policy violation cannot be repaired')
    turn.clock_context=observed
    return turn


def prepare(component, provider, checkpoint=None):
    component = Path(component).resolve()
    summary = _read(component / 'summary.json')
    source = (component / summary['source']).resolve()
    messages, _, resolved = read_prepared(source, 150)
    if checkpoint is not None and hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=summary['input_hash']:
        raise ValueError('Saved recovery source differs from the frozen input hash')
    plan, checks = load_evaluations(component, messages, resolved)
    if checkpoint is not None and plan['plan_hash']!=summary['evaluation_plan_hash']:
        raise ValueError('Saved recovery evaluation plan differs')
    arm = component / 'repeat-1' / provider
    report = _read(arm / 'evaluation.json')
    if [r['id'] for r in report['checks']] != [r['id'] for r, _, _ in checks]:
        raise ValueError('Saved evaluation differs from frozen plan')
    index=len(checks)-1 if checkpoint is None else next(i for i,(r,_,_) in enumerate(checks) if r['id']==checkpoint)
    row, contract, policy = checks[index]
    if (any(r['state'] != 'diagnostic' or r.get('recovery', {}).get('state', 'complete') != 'complete'
            for i,r in enumerate(report['checks']) if i!=index) or report['checks'][index]['state'] != ('ungraded' if checkpoint is None else 'failed')):
        raise ValueError('Only one missing terminal judgment can be recovered')
    directory = arm / 'turns' / row['id']
    if any((directory / name).exists() for name in ('judgment.json', 'grade.json', 'followups/recovery.json')):
        raise ValueError('A successful or partially graded checkpoint already exists')
    turn = harness.TurnResult(**_read(directory / 'turn.json'))
    if checkpoint is not None:turn=normalize_native_continuation(arm,turn)
    if (turn.returncode or turn.policy_blocks or not turn.answer.strip() or not turn.session_id
            or turn.at != row['at'] or turn.prompt != harness.question_prompt(contract.question, row['at'], summary['question_prompt_protocol'])):
        raise ValueError('Saved answer execution, policy or clock is invalid')
    # A fresh terminal session avoids reconstructing or silently changing the
    # inherited public conversation and its source-fallback history.
    if any(session_key(r, plan['context']) == session_key(row, plan['context']) for i,(r, _, _) in enumerate(checks) if i!=index):
        raise ValueError('Terminal answer must have an independent frozen session')
    source_state = _read(directory / 'source-state.json')
    receipts = sorted((arm / 'ingestion').glob('*.json'))
    if (source_state['at'] != row['at'] or not receipts
            or [p.stem for p in receipts] != source_state['ingested_batches']
            or any(_read(p).get('ready') is not True for p in receipts)):
        raise ValueError('Complete import receipts are required')
    files = [component / 'summary.json', component / 'evaluation-plan.json',
             component / 'evaluations' / row['id'] / 'contract.json',
             component / 'evaluations' / row['id'] / 'followup-plan.json',
             arm / 'evaluation.json', directory / 'turn.json', directory / 'status.json',
             directory / 'source-state.json', directory / 'usage.json', *receipts]
    if checkpoint is not None:
        files += [arm/name for name in ('api-inputs.jsonl','context.jsonl','provider-clock.json',
                    'provider-clock-startup.json','diagnostic-store-reopen.json','tool-policy.json')]
    for i,(previous, _, _) in enumerate(checks):
        if i==index:continue
        d = arm / 'turns' / previous['id']
        files += [d / 'status.json', d / 'turn.json', d / 'grade.json']
        files += [p for p in (d / 'followups/recovery.json', d / 'behavior.json') if p.exists()]
    bindings = {str(p.relative_to(component)): _sha(p) for p in files}
    evidence = judge_evidence(component, messages, resolved, row, plan['context'],
                              diagnostic_version=summary['evidence_diagnostic_protocol'])
    return summary, row, contract, policy, turn, evidence, bindings


def run(component, provider, overlay, judge, *, checkpoint=None):
    component, overlay = Path(component).resolve(), Path(overlay).resolve()
    summary, row, contract, policy, turn, evidence, bindings = prepare(component, provider,checkpoint)
    if overlay.parent != component / 'reviews' or overlay.exists():
        raise ValueError('Use a new direct child of the original run reviews directory')
    overlay.mkdir(parents=True)
    manifest = {'version': 'saved-terminal-completion-v1', 'provider': provider,
                'checkpoint': row['id'], 'source_files': bindings,
                'scope': 'New judge only; original provider answer and completed checks preserved'}
    save(overlay / 'manifest.json', manifest)
    if checkpoint is not None:
        manifest.update(version='saved-native-continuation-completion-v1',observer_fix='Pinned stock network continuation; original question, answer, native tools and chronology unchanged')
        save(overlay/'manifest.json',manifest);save(overlay/'normalized-turn.json',vars(turn))
    grade = grade_native_semantic(row['id'], judge_question(turn), turn.answer,
        tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
        record_path=overlay / 'judgment.json', assess_recovery=True,
        assess_interaction='session' in row,
        diagnostic_inputs=diagnostic_inputs(turn, contract, evidence,
            diagnostic_version=summary['evidence_diagnostic_protocol']),
        diagnostic_version=summary['evidence_diagnostic_protocol'], judge_protocol=summary['judge_protocol'])
    save(overlay / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
        'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots],
        'recovery': grade.recovery, 'interaction': grade.interaction,
        'evidence_diagnostic': grade.evidence_diagnostic})
    selected, _ = natural_eligibility(grade)
    if selected:
        save(overlay / 'result.json', {'state': 'followup-required', 'provider_calls': 0,
            'eligible_claim_ids': selected, 'reason': 'Requires explicit native-session recovery; saved-only recovery stops here'})
        return _read(overlay / 'result.json')
    def no_provider(*args, **kwargs):
        raise RuntimeError('Saved-answer recovery cannot execute provider calls')
    recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
        output=overlay / 'followups', judge=judge, home=component / 'repeat-1' / provider / 'hermes-home',
        allowlist='', extra_env={}, run_turn=no_provider,
        provenance_version=summary['provenance_version'], evidence_by_id=evidence,
        diagnostic_version=summary['evidence_diagnostic_protocol'], judge_protocol=summary['judge_protocol'])
    if any(_sha(component / p) != sha for p, sha in bindings.items()):
        raise ValueError('Original run changed during recovery')
    result = {'state': 'complete', 'provider': provider, 'checkpoint': row['id'],
              'provider_calls': 0, 'original_files_unchanged': True, 'score': recovery['score']}
    save(overlay / 'result.json', result)
    return result


def publish(component,provider,overlay):
    """Publish the audited observer correction; preserve the original bytes."""
    import shutil
    component,overlay=Path(component).resolve(),Path(overlay).resolve()
    manifest=_read(overlay/'manifest.json');result=_read(overlay/'result.json')
    if (overlay.parent!=component/'reviews' or manifest['provider']!=provider or result['provider']!=provider or
            manifest['checkpoint']!=result['checkpoint'] or manifest['version']!='saved-native-continuation-completion-v1' or result['state']!='complete'
            or result['provider_calls']!=0 or any(_sha(component/p)!=sha for p,sha in manifest['source_files'].items())):
        raise ValueError('Unchanged source evidence and complete saved judgment required')
    arm=component/'repeat-1'/provider;directory=arm/'turns'/manifest['checkpoint']
    if (directory/'grade.json').exists() or (overlay/'publication.json').exists():raise ValueError('Checkpoint already published')
    for name in ('turn.json','status.json'):shutil.copyfile(directory/name,overlay/('original-'+name))
    status=_read(directory/'status.json');grade=_read(overlay/'grade.json');recovery=_read(overlay/'followups/recovery.json')
    status.update(state='diagnostic',verdict='pass' if not grade['missed'] and not grade['misrepresented'] else 'fail',reason='',
        interaction=grade['interaction'],recovery={'state':recovery['state'],'score':recovery['score'],'attempts':recovery['attempts']})
    for source,target in [('normalized-turn.json','turn.json'),('grade.json','grade.json'),('judgment.json','judgment.json')]:
        shutil.copyfile(overlay/source,directory/target)
    shutil.copytree(overlay/'followups',directory/'followups');save(directory/'status.json',status)
    evaluation=_read(arm/'evaluation.json')
    evaluation['checks']=[status if r['id']==manifest['checkpoint'] else r for r in evaluation['checks']]
    evaluation['state']='diagnostic' if all(r['state']=='diagnostic' for r in evaluation['checks']) else 'incomplete'
    save(arm/'evaluation.json',evaluation)
    save(overlay/'publication.json',{'protocol':'saved-native-observer-correction-publication-v1',
        'original_evidence_preserved':True,'native_provider_calls':0,'answer_text_changed':False,'native_stores_changed':False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', type=Path)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--checkpoint',help='Independent failed checkpoint rejected only for a pinned native network continuation')
    parser.add_argument('--publish',action='store_true')
    args = parser.parse_args()
    # Validate before installing transport or creating any paid call.
    prepare(args.component, args.provider,args.checkpoint)
    from .openrouter_direct import direct_environment
    from .gateway_client import client_settings
    from .model_calls import ModelClient
    from .openrouter_route import MODEL
    key = os.environ['OPENROUTER_API_KEY']
    with direct_environment(key):
        base, token = client_settings('pacbench-judge')
        judge = ModelClient(key=token, model=MODEL, url=base + '/chat/completions',
                            record_dir=(args.component/'model-calls' if args.checkpoint else args.overlay / 'model-calls'), reasoning_effort='low',
                            json_mode=True, timeout_s=600)
        result=run(args.component, args.provider, args.overlay, judge,checkpoint=args.checkpoint)
        if args.publish and result['state']=='complete':publish(args.component,args.provider,args.overlay)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
