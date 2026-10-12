"""Chronological provider-only conversations; independent failures stay local."""
from __future__ import annotations

import json
from . import harness
from .usage import tracked_turn
from .followups import audit_followups, run_followups, save, tool_profile, initial_effort
from .semantic_grade import Claim, grade_semantic, BenchmarkContractError


def extend_history(history, turn):
    return history + [{'role': 'user', 'content': turn.prompt},
                      {'role': 'assistant', 'content': turn.answer}]


def extend_path(inherited, turn):
    profile = tool_profile(turn)
    return {'source_fallback': inherited['source_fallback'] or profile['source_fallback_attempted'],
            'unclassified': inherited['unclassified'] or bool(profile['unclassified_operations'])}


def session_key(row, context):
    return row.get('session') or ('shared' if context == 'shared' else row['id'])


def fresh_state():
    return {'history': [], 'previous': None, 'session': None,
            'path': {'source_fallback': False, 'unclassified': False}, 'failed': None}


def advance(state, turn, directory):
    state['history'] = extend_history(state['history'], turn)
    state['path'] = extend_path(state['path'], turn)
    state['previous'], state['session'] = turn, turn.session_id
    follow = directory / 'followups/combined/turn.json'
    if follow.exists():
        next_turn = harness.TurnResult(**json.loads(follow.read_text()))
        state['history'] = extend_history(state['history'], next_turn)
        state['path'] = extend_path(state['path'], next_turn)
        state['previous'] = next_turn


def run_checks(checks, context, root, judge, allowlist, env, *, question_dir,
               run_turn=None, budget_snapshot=None, before_check=None):
    run_turn = run_turn or harness.run_turn
    sessions, outcomes = {}, {}
    report = {'state': 'running', 'context': context, 'checks': []}
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'evaluation.json').open('x') as record:
        json.dump(report, record)
    for row, contract, policy in checks:
        key = session_key(row, context)
        state = sessions.setdefault(key, fresh_state())
        directory = root / 'turns' / row['id']
        item = {'id': row['id'], 'at': row['at'], 'session': key, 'contract_hash': contract.contract_hash,
                'state': 'ungraded', 'verdict': None, 'reason': 'turn not complete'}
        report['checks'].append(item); outcomes[row['id']] = item
        save(root / 'evaluation.json', report)
        dependency = outcomes.get(row.get('depends_on'))
        missing=next((outcomes[r] for r in row.get('requires',[]) if outcomes[r]['state']!='diagnostic'),None)
        if state['failed'] or dependency and dependency['state'] != 'diagnostic' or missing:
            item.update(state='skipped', reason='Dependency unavailable', cause=state['failed'] or (missing or dependency)['id'])
        elif row.get('trigger') == 'clarification' and dependency.get('interaction') != 'clarification':
            item.update(state='skipped', reason='Scripted clarification reply was not requested', cause=dependency['id'])
        else:
            turn = None
            try:
                if before_check is not None:before_check(row, directory)
                turn = tracked_turn(run_turn, contract.question, usage_path=directory / 'usage.json', at=row['at'],
                    home=root / 'hermes-home', allowlist=allowlist, extra_env=env, timeout_s=180,
                    session_id=state['session'])
                save(directory / 'turn.json', vars(turn))
                if turn.policy_blocks:
                    item.update(state='failed', verdict='fail', reason='Disallowed tool attempted')
                    state['failed'] = row['id']
                elif (turn.returncode or not turn.answer.strip() or not turn.session_id or
                        state['session'] and turn.session_id != state['session']):
                    item.update(reason='Hermes turn failed or lost conversation continuity', error_type='execution')
                    state['failed'] = row['id']
                else:
                    behavior = row.get('kind') == 'behavior'
                    interactive = 'session' in row or behavior
                    grade = grade_semantic(row['id'], turn.prompt, turn.answer,
                        tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
                        record_path=directory / 'judgment.json', conversation=state['history'],
                        assess_recovery=not behavior, assess_interaction=interactive)
                    save(directory / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
                        'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots],
                        'recovery': grade.recovery, 'interaction': grade.interaction})
                    item.update(verdict='pass' if grade.all_claims_correct else 'fail', interaction=grade.interaction)
                    if behavior:
                        # Scripted story replies and acknowledgements never get
                        # an omission-recovery question or persistence credit.
                        save(directory / 'behavior.json', {'state':'complete',
                            'initial_metrics':initial_effort(turn,state['previous'],inherited_path=state['path']),
                            'observable_only':True})
                        item.update(state='diagnostic', reason='')
                    else:
                        recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
                            output=directory / 'followups', judge=judge, home=root / 'hermes-home',
                            allowlist=allowlist, extra_env=env, question_dir=question_dir,
                            previous_turn=state['previous'], conversation=state['history'], inherited_path=state['path'],
                            run_turn=run_turn, budget_snapshot=budget_snapshot)
                        item.update(state='diagnostic',
                            reason='' if recovery['state']=='complete' else 'Recovery diagnostics incomplete',
                            recovery={'state':recovery['state'],'score':recovery['score'],'attempts':recovery['attempts']})
                    advance(state,turn,directory)
            except Exception as exc:
                # Keep completed public history even if its judge failed. An
                # execution failure poisons that session, not independent ones.
                if turn and not turn.returncode and not turn.policy_blocks and turn.session_id:
                    advance(state,turn,directory)
                elif turn is not None:state['failed'] = row['id']
                reason=f'{type(exc).__name__}: {exc}'
                token=env.get('OPENAI_API_KEY','')
                if token:reason=reason.replace(token,'<gateway-token>')
                item.update(state='ungraded',verdict=None,reason=reason,
                    error_type='benchmark_contract' if isinstance(exc,BenchmarkContractError) else 'execution_or_judge')
        save(directory / 'status.json', item)
        save(root / 'evaluation.json', report)
    report['state'] = ('diagnostic' if all((x['state']=='diagnostic' and
        x.get('recovery',{}).get('state','complete')=='complete') or
        x['state']=='skipped' and x['reason']=='Scripted clarification reply was not requested' for x in report['checks']) else 'partial')
    report['counts'] = {v:sum(x.get('verdict')==v for x in report['checks']) for v in ('pass','fail')}
    report['counts']['ungraded'] = sum(x.get('verdict') is None and (x['state']=='ungraded' or x['state']=='skipped' and x['reason']=='Dependency unavailable') for x in report['checks'])
    recovery_ungraded=sum(x.get('recovery',{}).get('state','complete')!='complete' for x in report['checks'])
    if recovery_ungraded:report['counts']['recovery_ungraded']=recovery_ungraded
    save(root / 'evaluation.json', report)
    return report


def audit_checks(checks, context, root):
    errors, sessions, seen = [], {}, {}
    def check(ok, text):
        if not ok:errors.append(text)
    def read(path):return json.loads(path.read_text())
    try:
        report=read(root/'evaluation.json')
        check(report['context']==context,'evaluation context mismatch')
        check([r['id'] for r in report['checks']]==[r['id'] for r,_,_ in checks],'evaluation checks differ from frozen plan')
        check(sorted(p.name for p in (root/'turns').iterdir())==sorted(r['id'] for r,_,_ in checks),'unexpected evaluation folders')
        outcomes={i['id']:i for i in report['checks']}
        for (row,contract,policy),item in zip(checks,report['checks']):
            key=session_key(row,context);state=sessions.setdefault(key,fresh_state())
            directory=root/'turns'/row['id']
            check(item==read(directory/'status.json'),f"{row['id']}: status mismatch")
            if item['state']=='skipped':
                dependency=outcomes.get(item.get('cause'),{})
                if item['reason']=='Scripted clarification reply was not requested':
                    check(row.get('trigger')=='clarification' and row.get('depends_on')==item['cause'] and
                        dependency.get('interaction')!='clarification',f"{row['id']}: invalid conditional skip")
                else:
                    check(item['reason']=='Dependency unavailable' and dependency.get('state')!='diagnostic',f"{row['id']}: unexplained dependency skip")
                check(not (directory/'turn.json').exists(),f"{row['id']}: skipped turn executed")
                continue
            if not (directory/'turn.json').exists():
                check(item['state']=='ungraded' and bool(item.get('reason')),f"{row['id']}: missing turn evidence")
                continue
            turn=harness.TurnResult(**read(directory/'turn.json'))
            expected=f"[Simulated now: {row['at']}. Treat this as the current date and time for everything in this turn.]\n\n{contract.question}"
            check(turn.at==row['at'] and turn.prompt==expected,f"{row['id']}: evaluation question/clock mismatch")
            if item['state']!='diagnostic':
                check(bool(item.get('reason')) and item.get('verdict') in (None,'fail'),f"{row['id']}: unexplained ungraded turn")
                if not turn.returncode and not turn.policy_blocks and turn.session_id:advance(state,turn,directory)
                continue
            check(not turn.returncode and not turn.policy_blocks and bool(turn.answer.strip()),f"{row['id']}: failed evaluation turn")
            if state['session']:check(turn.session_id==state['session'],f"{row['id']}: shared conversation changed")
            else:check(bool(turn.session_id) and turn.session_id not in seen,f"{row['id']}: evaluation reused an isolated session")
            seen[turn.session_id]=key
            judgment,grade=read(directory/'judgment.json'),read(directory/'grade.json')
            check(judgment['question']==turn.prompt and judgment['answer']==turn.answer and judgment.get('conversation')==state['history'],f"{row['id']}: judge conversation mismatch")
            response=json.loads(judgment['response'])
            check(response['verdicts']==grade['claims'] and response.get('recovery')==grade.get('recovery'),f"{row['id']}: grade differs from judge")
            check(judgment['claims']==[{'id':c.id,'description':c.statement,'evidence':list(c.evidence)} for c in contract.claims],f"{row['id']}: judge received different criteria")
            check(item['verdict']==('pass' if all(c['verdict']=='correct' for c in grade['claims']) else 'fail'),f"{row['id']}: pass/fail mismatch")
            if row.get('kind')=='behavior':
                check(not (directory/'followups').exists(),f"{row['id']}: scripted action got recovery")
                check(read(directory/'behavior.json')['initial_metrics']==initial_effort(turn,state['previous'],inherited_path=state['path']),f"{row['id']}: behavior cost mismatch")
            else:
                errors.extend(f"{row['id']}: {e}" for e in audit_followups(contract=contract,plan=policy,
                    initial_turn=turn,initial_grade=grade,output=directory/'followups',previous_turn=state['previous'],
                    conversation=state['history'],inherited_path=state['path']))
                recovery=read(directory/'followups/recovery.json')
                check(item['recovery']['score']==recovery['score'] and item['recovery']['attempts']==recovery['attempts'],f"{row['id']}: summary/score mismatch")
            advance(state,turn,directory)
    except (OSError,ValueError,TypeError,KeyError) as exc:errors.append(f'evaluation audit: {exc}')
    return errors
