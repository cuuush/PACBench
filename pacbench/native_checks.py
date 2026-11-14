"""Chronological provider-only conversations; independent failures stay local."""
from __future__ import annotations

import json
from . import harness
from .gateway_client import ModelAccessBlocked, SpendingBlocked
from .usage import tracked_turn
from .followups import audit_followups, run_followups, save, tool_profile, initial_effort
from .semantic_grade import Claim, grade_native_semantic, audit_native_judge_attempts, BenchmarkContractError, validate_diagnostic, DIAGNOSTIC_VERSIONS, LEGACY_DIAGNOSTIC_VERSION, LEGACY_JUDGE_PROTOCOL, judge_prompt
from .clock_context import judge_question


def extend_history(history, turn):
    return history + [{'role': 'user', 'content': judge_question(turn)},
                      {'role': 'assistant', 'content': turn.answer}]


def extend_path(inherited, turn, provenance_version='source-provenance-v1'):
    profile = tool_profile(turn, provenance_version=provenance_version)
    return {'source_fallback': inherited['source_fallback'] or profile['source_fallback_attempted'],
            'unclassified': inherited['unclassified'] or bool(profile['unclassified_operations'])}


def session_key(row, context):
    return row.get('session') or ('shared' if context == 'shared' else row['id'])


def fresh_state():
    return {'history': [], 'previous': None, 'session': None,
            'session_started_at': None,
            'path': {'source_fallback': False, 'unclassified': False}, 'failed': None}


def advance(state, turn, directory, provenance_version='source-provenance-v1'):
    if state['session_started_at'] is None:
        state['session_started_at'] = turn.session_started_at or turn.at
    state['history'] = extend_history(state['history'], turn)
    state['path'] = extend_path(state['path'], turn, provenance_version)
    state['previous'], state['session'] = turn, turn.session_id
    follow = directory / 'followups/combined/turn.json'
    if follow.exists():
        next_turn = harness.TurnResult(**json.loads(follow.read_text()))
        state['history'] = extend_history(state['history'], next_turn)
        state['path'] = extend_path(state['path'], next_turn, provenance_version)
        state['previous'] = next_turn



def diagnostic_inputs(turn, contract, evidence_by_id, *, prior_turn=None, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION):
    """Only captured inputs visible to this answer; native background traces are excluded."""
    limit = 16000
    def bounded(identifier, value, *, truncated=False):
        value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        return {'id': identifier, 'text': value[:limit], 'truncated': bool(truncated) or len(value) > limit}
    visible = []
    for current, prefix in ((prior_turn, 'initial-'), (turn, '')):
        if current is None: continue
        if current.prefetch_context is not None:
            visible.append(bounded(prefix + 'prefetch', current.prefetch_context))
        for i, result in enumerate(current.tool_results[:len(current.tool_calls)]):
            if isinstance(result, dict) and isinstance(result.get('text'), str):
                captured = bounded(f'{prefix}tool-result-{i + 1}', result['text'], truncated=result.get('truncated', False))
                for key in ('success', 'policy_blocked'):
                    if key in result: captured[key] = result[key]
                visible.append(captured)
    ids = list(dict.fromkeys(e for claim in contract.claims for e in claim.evidence))
    sources = [bounded(identifier, evidence_by_id[identifier]) for identifier in ids if identifier in evidence_by_id]
    return {'answer_visible': visible, 'source_evidence': sources,
            'source_scope': 'partial: expected-claim citations only',
            'missing_source_ids': [identifier for identifier in ids if identifier not in evidence_by_id],
            'answer_visible_scope': ('provider evidence returned to Hermes: captured prefetch and direct tool results only; user prompts, tool arguments and background traces excluded' if diagnostic_version != LEGACY_DIAGNOSTIC_VERSION else 'captured prefetch and direct tool results only; background traces excluded'),
            'per_item_character_limit': limit}

def run_checks(checks, context, root, judge, allowlist, env, *, question_dir, resume_prefix=0,
               run_turn=None, budget_snapshot=None, before_check=None, evidence_by_id=None, provenance_version='source-provenance-v1', diagnostic_version=LEGACY_DIAGNOSTIC_VERSION, judge_protocol=LEGACY_JUDGE_PROTOCOL, turn_timeout_s=180):
    from .stop import OperatorStop, check_stop
    native_turn = run_turn or harness.run_turn
    def run_turn(*args, **kwargs):
        check_stop(env.get('PACBENCH_STOP_FILE'))
        kwargs['timeout_s']=turn_timeout_s
        return native_turn(*args, **kwargs)
    sessions, outcomes = {}, {}
    report = {'state': 'running', 'context': context, 'checks': []}
    root.mkdir(parents=True, exist_ok=True)
    if resume_prefix:
        previous=json.loads((root/'evaluation.json').read_text())
        if previous['context']!=context or len(previous['checks'])!=resume_prefix:raise ValueError('Resume prefix differs')
        for (row,contract,_),item in zip(checks[:resume_prefix],previous['checks']):
            if item['id']!=row['id'] or item['state']!='diagnostic' or item.get('recovery',{}).get('state','complete')!='complete':raise ValueError('Resume requires complete diagnostic prefix')
            directory=root/'turns'/row['id'];turn=harness.TurnResult(**json.loads((directory/'turn.json').read_text()))
            key=session_key(row,context);state=sessions.setdefault(key,fresh_state())
            report['checks'].append(item);outcomes[row['id']]=item
            advance(state,turn,directory,provenance_version)
    else:
        with (root / 'evaluation.json').open('x') as record:json.dump(report, record)
    for row, contract, policy in checks[resume_prefix:]:
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
                    home=root / 'hermes-home', allowlist=allowlist, extra_env=env, timeout_s=turn_timeout_s,
                    session_id=state['session'], session_started_at=state['session_started_at'])
                save(directory / 'turn.json', vars(turn))
                check_stop(env.get('PACBENCH_STOP_FILE'))
                if turn.timed_out:
                    item.update(reason='Hermes turn exceeded real-time deadline',error_type='execution_timeout')
                    state['failed'] = row['id']
                elif turn.policy_blocks:
                    item.update(state='failed', verdict='fail', reason='Disallowed tool attempted')
                    state['failed'] = row['id']
                elif (turn.returncode or not turn.answer.strip() or not turn.session_id or
                        state['session'] and turn.session_id != state['session']):
                    item.update(reason='Hermes turn failed or lost conversation continuity', error_type='execution')
                    state['failed'] = row['id']
                else:
                    behavior = row.get('kind') == 'behavior'
                    interactive = 'session' in row or behavior
                    grade = grade_native_semantic(row['id'], judge_question(turn), turn.answer,
                        tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
                        record_path=directory / 'judgment.json', conversation=state['history'],
                        assess_recovery=not behavior, assess_interaction=interactive,
                        diagnostic_inputs=diagnostic_inputs(turn, contract, evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id, diagnostic_version=diagnostic_version) if evidence_by_id is not None else None, diagnostic_version=diagnostic_version, judge_protocol=judge_protocol)
                    save(directory / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
                        'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots],
                        'recovery': grade.recovery, 'interaction': grade.interaction,
                        **({'evidence_diagnostic': grade.evidence_diagnostic} if grade.evidence_diagnostic is not None else {})})
                    item.update(verdict='pass' if grade.all_claims_correct else 'fail', interaction=grade.interaction)
                    if behavior:
                        # Scripted story replies and acknowledgements never get
                        # an omission-recovery question or persistence credit.
                        save(directory / 'behavior.json', {'state':'complete',
                            'initial_metrics':initial_effort(turn,state['previous'],inherited_path=state['path'],provenance_version=provenance_version),
                            'observable_only':True})
                        item.update(state='diagnostic', reason='')
                    else:
                        recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
                            output=directory / 'followups', judge=judge, home=root / 'hermes-home',
                            allowlist=allowlist, extra_env=env, question_dir=question_dir,
                            previous_turn=state['previous'], conversation=state['history'], inherited_path=state['path'],
                            run_turn=run_turn, budget_snapshot=budget_snapshot, provenance_version=provenance_version,
                            evidence_by_id=(evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id), diagnostic_version=diagnostic_version, judge_protocol=judge_protocol)
                        item.update(state='diagnostic',
                            reason='' if recovery['state']=='complete' else 'Recovery diagnostics incomplete',
                            recovery={'state':recovery['state'],'score':recovery['score'],'attempts':recovery['attempts']})
                    advance(state,turn,directory,provenance_version)
            except (ModelAccessBlocked,SpendingBlocked) as exc:
                item.update(state='ungraded',verdict=None,error_type='access_blocked' if isinstance(exc,ModelAccessBlocked) else 'budget_blocked',reason=str(exc))
                report.update(state=item['error_type'],reason=str(exc))
                save(directory/'status.json',item);save(root/'evaluation.json',report)
                return report
            except OperatorStop:
                item.update(state='interrupted', verdict=None, reason='operator stop requested')
                save(directory / 'status.json', item)
                report['state'] = 'interrupted'
                save(root / 'evaluation.json', report)
                return report
            except Exception as exc:
                # Keep completed public history even if its judge failed. An
                # execution failure poisons that session, not independent ones.
                if turn and not turn.returncode and not turn.policy_blocks and turn.session_id:
                    advance(state,turn,directory,provenance_version)
                else:state['failed'] = row['id']
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


def audit_checks(checks, context, root, *, provenance_version='source-provenance-v1', evidence_by_id=None, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION, judge_protocol=LEGACY_JUDGE_PROTOCOL, clock_context_protocol='user-clock-prefix-v1', prompt_protocol=None):
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
                state['failed']=row['id']
                continue
            check(not state['failed'],f"{row['id']}: executed after an unavailable turn in the same conversation")
            turn=harness.TurnResult(**read(directory/'turn.json'))
            from .usage import provider_paths_match
            if (directory/'usage.json').exists():
                check(provider_paths_match(turn,read(directory/'usage.json')),
                    f"{row['id']}: provider path timing differs from recorded inference inputs")
            from .clock_context import expected_prompt
            expected=expected_prompt(row['at'],contract.question,clock_context_protocol)
            if prompt_protocol is not None:expected=harness.question_prompt(contract.question,row['at'],prompt_protocol)
            check(turn.at==row['at'] and turn.prompt==expected,f"{row['id']}: evaluation question/clock mismatch")
            if judge_protocol != LEGACY_JUDGE_PROTOCOL and (directory/'judgment.json').exists():
                claim_objects = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
                inputs = diagnostic_inputs(turn, contract, evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id, diagnostic_version=diagnostic_version) if evidence_by_id is not None else None
                audit_native_judge_attempts(directory/'judgment.json', question=judge_question(turn), answer=turn.answer,
                    claims=claim_objects, judge_protocol=judge_protocol, conversation=state['history'],
                    assess_recovery=row.get('kind') != 'behavior', assess_interaction='session' in row or row.get('kind') == 'behavior',
                    diagnostic_inputs=inputs, diagnostic_version=diagnostic_version)
            if item['state']!='diagnostic':
                check(bool(item.get('reason')) and item.get('verdict') in (None,'fail'),f"{row['id']}: unexplained ungraded turn")
                if not turn.returncode and not turn.policy_blocks and turn.answer.strip() and turn.session_id:advance(state,turn,directory,provenance_version)
                else:state['failed']=row['id']
                continue
            check(not turn.returncode and not turn.policy_blocks and bool(turn.answer.strip()),f"{row['id']}: failed evaluation turn")
            if state['session']:check(turn.session_id==state['session'],f"{row['id']}: shared conversation changed")
            else:check(bool(turn.session_id) and turn.session_id not in seen,f"{row['id']}: evaluation reused an isolated session")
            seen[turn.session_id]=key
            judgment,grade=read(directory/'judgment.json'),read(directory/'grade.json')
            check(judgment.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) == judge_protocol, f"{row['id']}: judge protocol mismatch")
            check(judgment['question']==judge_question(turn) and judgment['answer']==turn.answer and judgment.get('conversation')==state['history'],f"{row['id']}: judge conversation mismatch")
            response=json.loads(judgment['response'])
            check(response['verdicts']==grade['claims'] and response.get('recovery')==grade.get('recovery'),f"{row['id']}: grade differs from judge")
            if evidence_by_id is not None:
                check('diagnostic_protocol' in judgment and 'evidence_diagnostic' in grade, f"{row['id']}: missing evidence diagnostic")
            if 'diagnostic_protocol' in judgment or 'evidence_diagnostic' in grade:
                inputs = judgment.get('diagnostic_inputs', {})
                check(diagnostic_version in DIAGNOSTIC_VERSIONS and judgment.get('diagnostic_protocol') == diagnostic_version, f"{row['id']}: unsupported diagnostic protocol")
                claim_objects = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
                expected_prompt = judge_prompt(judge_question(turn), turn.answer, claim_objects, conversation=state['history'],
                    assess_recovery=row.get('kind') != 'behavior', assess_interaction='session' in row or row.get('kind') == 'behavior',
                    diagnostic_inputs=inputs, diagnostic_version=diagnostic_version, judge_protocol=judge_protocol)
                check(judgment.get('prompt') == expected_prompt + judgment.get('prompt_suffix', ''), f"{row['id']}: diagnostic judge prompt mismatch")
                check(grade.get('evidence_diagnostic') == validate_diagnostic(response.get('evidence_diagnostic'), claim_objects, turn.answer, inputs, diagnostic_version), f"{row['id']}: diagnostic differs from judge")
                expected_inputs = diagnostic_inputs(turn, contract, {x['id']: x['text'] for x in inputs.get('source_evidence', [])}, diagnostic_version=diagnostic_version)
                if evidence_by_id is not None:
                    actual_sources = evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id
                    check(inputs == diagnostic_inputs(turn, contract, actual_sources, diagnostic_version=diagnostic_version), f"{row['id']}: diagnostic differs from frozen evidence")
                check(inputs.get('answer_visible') == expected_inputs['answer_visible'] and
                    all(x.get('id') in {e for c in contract.claims for e in c.evidence} for x in inputs.get('source_evidence', [])),
                    f"{row['id']}: diagnostic evidence scope mismatch")
            check(judgment['claims']==[{'id':c.id,'description':c.statement,'evidence':list(c.evidence)} for c in contract.claims],f"{row['id']}: judge received different criteria")
            check(item['verdict']==('pass' if all(c['verdict']=='correct' for c in grade['claims']) else 'fail'),f"{row['id']}: pass/fail mismatch")
            if row.get('kind')=='behavior':
                check(not (directory/'followups').exists(),f"{row['id']}: scripted action got recovery")
                check(read(directory/'behavior.json')['initial_metrics']==initial_effort(turn,state['previous'],inherited_path=state['path'],provenance_version=provenance_version),f"{row['id']}: behavior cost mismatch")
            else:
                errors.extend(f"{row['id']}: {e}" for e in audit_followups(contract=contract,plan=policy,
                    initial_turn=turn,initial_grade=grade,output=directory/'followups',previous_turn=state['previous'],
                    conversation=state['history'],inherited_path=state['path'],provenance_version=provenance_version,
                    evidence_by_id=(evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id), diagnostic_version=diagnostic_version, judge_protocol=judge_protocol, clock_context_protocol=clock_context_protocol, prompt_protocol=prompt_protocol))
                recovery=read(directory/'followups/recovery.json')
                check(item['recovery']['score']==recovery['score'] and item['recovery']['attempts']==recovery['attempts'],f"{row['id']}: summary/score mismatch")
            advance(state,turn,directory,provenance_version)
    except (OSError,ValueError,TypeError,KeyError) as exc:errors.append(f'evaluation audit: {exc}')
    return errors
