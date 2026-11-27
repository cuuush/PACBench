"""Offline integrity checks for native ingestion/recall trials, without scoring text."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pacbench import harness
from pacbench.context_isolation import judge_question
from pacbench.corpus.contract import load_contract
from pacbench.corpus.evaluation import load_evaluations
from pacbench.corpus.sources import load_bundle, messages_for
from pacbench.judging.grade import (
    DIAGNOSTIC_VERSIONS,
    LEGACY_DIAGNOSTIC_VERSION,
    LEGACY_JUDGE_PROTOCOL,
    Claim,
    audit_native_judge_attempts,
    judge_prompt,
    validate_diagnostic,
)
from pacbench.judging.recovery import audit_followups, initial_effort, load_questions
from pacbench.workflows.turn_evidence import (
    advance,
    diagnostic_inputs,
    fresh_state,
    session_key,
)

from .harness import (
    TurnResult,
)
from .source_policy import SUPPORTED_VERSIONS, policy_record, source_read_blocked


def audit(root: Path, *, providers=None) -> dict:
    errors = []
    selected=set(providers) if providers is not None else None
    def read(path):
        return json.loads(path.read_text())
    def check(ok, message):
        if not ok:
            errors.append(message)
    def clock_prompt(at, text):
        if summary.get('question_prompt_protocol') is not None:
            from .harness import question_prompt
            return question_prompt(text, at, summary['question_prompt_protocol'])
        from pacbench.context_isolation import LEGACY_CLOCK_PREFIX as LEGACY
        from pacbench.context_isolation import expected_prompt
        return expected_prompt(at,text,summary.get('clock_context_protocol',LEGACY))
    try:
        summary = read(root / 'summary.json')
        from pacbench.context_isolation import (
            CLOCK_CONTEXT_PROTOCOLS as CLOCK_CONTEXT_PROTOCOLS,
        )
        from pacbench.context_isolation import LEGACY_CLOCK_PREFIX as LEGACY
        from pacbench.context_isolation import NATIVE_QUESTION as NATIVE
        from pacbench.context_isolation import SYSTEM_CLOCK_PROTOCOL as CLOCK_CONTEXT
        clock_protocol=summary.get('clock_context_protocol',LEGACY)
        if clock_protocol not in CLOCK_CONTEXT_PROTOCOLS:raise ValueError('Unknown clock context protocol')
        from .harness import LEGACY_PROMPT_PROTOCOL, PROMPT_PROTOCOL
        prompt_protocol=summary.get('question_prompt_protocol')
        if prompt_protocol not in (None,PROMPT_PROTOCOL,LEGACY_PROMPT_PROTOCOL):
            raise ValueError('Unsupported question prompt protocol')
        if clock_protocol==NATIVE and prompt_protocol!=PROMPT_PROTOCOL:
            raise ValueError('Native clock context requires native question prompt protocol')
        if prompt_protocol==PROMPT_PROTOCOL and clock_protocol==CLOCK_CONTEXT:
            raise ValueError('Native question condition cannot include ephemeral benchmark instructions')
        from pacbench.judging.grade import (
            DIAGNOSTIC_VERSIONS,
            JUDGE_PROTOCOLS,
            LEGACY_DIAGNOSTIC_VERSION,
            LEGACY_JUDGE_PROTOCOL,
        )
        if summary.get('provenance_version', 'source-provenance-v1') not in {'source-provenance-v1', 'source-provenance-v2', 'source-provenance-v3'}:
            raise ValueError('Unsupported provenance protocol')
        if 'evidence_diagnostic_protocol' in summary and summary['evidence_diagnostic_protocol'] not in DIAGNOSTIC_VERSIONS:
            raise ValueError('Unsupported evidence diagnostic protocol')
        if summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) not in JUDGE_PROTOCOLS:
            raise ValueError('Unsupported semantic judge protocol')
        judge_conditions=summary.get('judge_generation_conditions')
        if judge_conditions is not None:
            if not isinstance(judge_conditions, dict) or not judge_conditions:
                raise ValueError('Invalid benchmark judge generation condition')
            if 'reasoning_effort' in judge_conditions and (
                    summary.get('inference_route') != 'openrouter' or
                    judge_conditions['reasoning_effort'] not in ('none', 'low', 'medium', 'high')):
                raise ValueError('Invalid benchmark judge generation condition')
            if 'wall_deadline_s' in judge_conditions:
                deadline = judge_conditions['wall_deadline_s']
                if type(deadline) is not int or not 0 < deadline <= 900:
                    raise ValueError('Invalid benchmark judge generation condition')
        check(summary.get('ingestion_mode') == 'documents', 'Native source ingestion required')
        check(summary.get('tool_policy') in SUPPORTED_VERSIONS, 'Provider tool boundary required')
        bundle = load_bundle(root / 'bundle.json', summary.get('bundle_hash') or read(root / 'bundle.json')['bundle_hash'])
        check(len(bundle['lives']) == 1, 'expected one life')
        messages = messages_for(bundle, next(iter(bundle['lives'])))
        contract = load_contract(root / 'contract.json')
        plan = (load_questions(root / 'followup-plan.json', contract)
                if summary.get('followup_plan_hash') or (root / 'followup-plan.json').exists() else None)
        if plan:
            check(plan['plan_hash'] == summary['followup_plan_hash'], 'summary follow-up plan mismatch')
        spec = read(root / 'resolved-spec.json')
        evaluations = []
        if (root / 'evaluation-plan.json').exists():
            evaluation_plan, evaluations = load_evaluations(root, messages, spec)
            check(evaluation_plan['plan_hash'] == summary.get('evaluation_plan_hash'), 'evaluation plan summary mismatch')
        if 'process_timezone' in summary:
            check(summary['process_timezone'] == spec.get('timezone', ''),
                  'process timezone differs from frozen scenario')
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        check(digest == summary['input_hash'] and (bool(evaluations) or digest == contract.evidence_hash), 'source hash mismatch')
        check(contract.contract_hash == summary['contract_hash'], 'contract hash mismatch')
        check(len(messages) == summary['message_count'], 'message count mismatch')
        check(bool(summary['results']), 'no provider results')
        if selected is not None:
            check(bool(selected) and selected<={r['provider'] for r in summary['results']},
                  'selected providers missing from run')
        for result in summary['results']:
            if selected is not None and result['provider'] not in selected:continue
            label = f"repeat-{result.get('repetition', '?')}/{result.get('provider', '?')}"
            try:
                arm = root / f"repeat-{result['repetition']}" / result['provider']
                label = str(arm.relative_to(root))
                if summary.get('ingestion_mode')=='documents' and result['state']=='blocked':
                    from pacbench.workflows.delivery import BLOCKERS
                    blockers=summary.get('document_provider_blockers',BLOCKERS)
                    check(result['reason']==blockers.get(result['provider']),f'{label}: unverified blocked-provider reason')
                    check(not list((arm/'ingestion').glob('*.json')) and not (arm/'recall.json').exists(),f'{label}: blocked arm executed')
                    continue
                if 'process_timezone' in summary:
                    check(read(arm / 'simulation-clock.json')['process_timezone'] == summary['process_timezone'],
                          f'{label}: inconsistent provider process timezone')
                # A provider's missed clarification can legitimately leave later
                # story branches undelivered. Audit their causes and all live traces;
                # partial story coverage is separate from trace integrity.
                partial_story = (bool(evaluations) and result['state'] == 'partial'
                    and bool(result.get('checks'))
                    and any(c['state'] == 'diagnostic' for c in result['checks'])
                    and all(c['state'] in ('diagnostic', 'skipped') for c in result['checks']))
                reviewable = result['state'] == 'diagnostic' or partial_story
                check(reviewable, f'{label}: incomplete arm')
                check(result.get('input_hash') == digest, f'{label}: arm hash mismatch')
                check(result['capability'].get('wiring_verified'), f'{label}: wiring not verified')
                terminal=summary.get('terminal_processing_protocols',{}).get(result['provider'])
                if terminal is not None:
                    from pacbench.native_writes import (
                        TERMINAL_PROTOCOL as TERMINAL_PROTOCOL,
                    )
                    from pacbench.native_writes import audit_terminal
                    check(result['provider']=='openviking' and terminal==TERMINAL_PROTOCOL,
                          f'{label}: unsupported native terminal processing condition')
                    for error in audit_terminal(arm):check(False,f'{label}: '+error)
                if summary.get('provider_loader_protocol')=='native-loaded-source-v1' and result['provider'] in {'reme','cognee','memos'}:
                    setup=read(arm/'native-server-setup.json')
                    expected_name='memtensor' if result['provider']=='memos' else result['provider']
                    check(result['capability'].get('loaded_native_provider')==expected_name and
                          result['capability'].get('loaded_source_sha256')==setup.get('plugin_source_sha256') and
                          bool(result['capability'].get('loaded_provider_module')),
                          f'{label}: actual loaded native plugin differs from declared pinned source')
                if not reviewable and not ((arm/'recall.json').exists() or list((arm/'turns').glob('*/turn.json'))):
                    # Unfinished arms still need their existing live traces
                    # checked; only a genuinely unexecuted arm has none.
                    continue
                if summary.get('tool_policy') in SUPPORTED_VERSIONS:
                    from .context_isolation import VERSION, actual_context
                    workspace_file = arm / 'recall-workspace.json'
                    workspace_record = (read(workspace_file) if workspace_file.exists()
                                        else {'version': 'missing', 'workspace': '/unrecorded'})
                    check(workspace_record['version'] == VERSION,
                          f'{label}: unsupported recall context isolation')
                    check(summary.get('builtin_memory') is False, f'{label}: built-in memory enabled')
                    expected_policy = policy_record(result['provider'], result['capability']['tools'],
                        version=summary['tool_policy'])
                    allowed = expected_policy['allowed_tools']
                    policy = read(arm / 'tool-policy.json')
                    rules = expected_policy['source_argument_rules']
                    check(policy == expected_policy,
                        f'{label}: provider tool policy mismatch')
                    check((bool(allowed) or result['provider']=='reme' and
                          policy.get('native_mode')=='automatic-prefetch-only') and all('*' not in n for n in allowed),
                        f'{label}: missing exact provider tool names')
                    schemas = [json.loads(line) for line in
                        (arm / 'context.jsonl.tools.jsonl').read_text().splitlines()]
                    check(bool(schemas), f'{label}: missing offered tool evidence')
                    check(all(row['allowed_tools'] == allowed and
                        set(row['offered_tools']) <= set(allowed) and
                        row.get('source_argument_rules') == rules for row in schemas),
                        f'{label}: non-provider schema exposed')
                    paths = [*arm.glob('recall.json'), *arm.glob('turns/*/turn.json'),
                        *arm.glob('followups/*/turn.json'), *arm.glob('turns/*/followups/*/turn.json')]
                    for path in paths:
                        data = read(path)
                        if isinstance(data, dict) and 'tool_calls' in data and 'prompt' in data:
                            judgment_path=path.parent/'judgment.json'
                            if judge_conditions and 'reasoning_effort' in judge_conditions and judgment_path.exists():
                                judgment=read(judgment_path)
                                call=root/'model-calls'/Path(judgment.get('model_call') or '').name
                                check(call.is_file() and read(call).get('request',{}).get('reasoning_effort')==
                                      judge_conditions['reasoning_effort'],
                                      f'{label}: judge request differs from declared generation condition')
                            if clock_protocol==CLOCK_CONTEXT or (clock_protocol==NATIVE and
                                    summary.get('inference_route')=='openrouter'):
                                from pacbench.context_isolation import delivery
                                saved=data.get('clock_context') or {}
                                observed=delivery(arm,TurnResult(**data),identifiers=saved.get('api_input_ids',[]),
                                    hook_index=saved.get('native_query_index'),protocol=clock_protocol)
                                check(observed.get('state')=='pass' and observed==saved,
                                      f'{label}: actual system clock or natural provider query is unproven')
                            if summary.get('provenance_version')=='source-provenance-v3' and result['provider'] in {'cognee','memos','supermemory'}:
                                from pacbench.source_policy import (
                                    observe_prefetch as observe,
                                )
                                declaration=read(arm/'prefetch-provenance.json')
                                check(declaration.get('provider')==result['provider'] and
                                      data.get('prefetch_provenance')==observe(declaration,data.get('prefetch_context')),
                                      f'{label}: native prefetch provenance is missing or differs from actual context')
                            context = actual_context(arm / 'hermes-home', data.get('session_id'),
                                Path(workspace_record['workspace']),
                                prompt_hash=(data.get('context_isolation') or {}).get('system_prompt_sha256'),
                                receipt_version=(data.get('context_isolation') or {}).get('version', 'empty-recall-workspace-v1'))
                            check(context['state'] == 'pass',
                                  f'{label}: {context.get("reason", "recall context isolation failed")}')
                            check(data.get('context_isolation') == context,
                                  f'{label}: saved recall context receipt differs from native prompt')
                            check(not data.get('policy_blocks'),
                                  f'{label}: provider/context/clock boundary failed in {path.relative_to(arm)}')
                            check(all(
                                c.get('name') in allowed and not source_read_blocked(
                                    c.get('name'),c.get('input',c.get('arguments',{})),rules) for c in data['tool_calls']),
                                f'{label}: non-provider tool attempted in {path.relative_to(arm)}')
                from .document_audit import audit_ingestion
                try:errors.extend(f'{label}: {e}' for e in audit_ingestion(root,arm,messages,summary))
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    errors.append(f'{label}: ingestion audit {type(exc).__name__}')
                if evaluations:
                    from pacbench.corpus.evaluation import judge_evidence
                    def evidence_for_check(row):
                        return judge_evidence(root, messages, spec, row, evaluation_plan['context'], diagnostic_version=summary.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION))
                    errors.extend(f'{label}: {e}' for e in audit_checks(
                        evaluations, evaluation_plan['context'], arm,
                        provenance_version=summary.get('provenance_version', 'source-provenance-v1'),
                        evidence_by_id=evidence_for_check if summary.get('evidence_diagnostic_protocol') else None,
                        diagnostic_version=summary.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION),
                        judge_protocol=summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL),
                        clock_context_protocol=clock_protocol,prompt_protocol=prompt_protocol))
                    from .native_writes import audit_ready
                    errors.extend(f'{label}: {e}' for e in audit_ready(evaluations,evaluation_plan['context'],arm,
                        result.get('write_readiness_protocol',summary.get('write_readiness_protocol'))))
                    check(result['checks'] == read(arm / 'evaluation.json')['checks'], f'{label}: evaluation summary mismatch')
                    continue
                recall = read(arm / 'recall.json')
                from types import SimpleNamespace
                recall_turn=SimpleNamespace(**recall)
                check(recall['prompt'] == clock_prompt(spec['question_at'], contract.question), f'{label}: recall/clock mismatch')
                check(recall['at'] == spec['question_at'], f'{label}: recall time mismatch')
                check(bool(recall['session_id']), f'{label}: recall session missing')
                check(not recall['returncode'] and not recall['policy_blocks'], f'{label}: recall failure')
                judgment = read(arm / 'judgment.json')
                check(judgment.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) == summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL), f'{label}: judge protocol mismatch')
                check(judgment['question'] == judge_question(recall_turn), f'{label}: judge lacks recall clock context')
                check(judgment['answer'] == recall['answer'], f'{label}: judge answer mismatch')
                grade = read(arm / 'grade.json')
                check(sorted(c['id'] for c in grade['claims']) == sorted(c.id for c in contract.claims), f'{label}: incomplete judgments')
                if summary.get('evidence_diagnostic_protocol'):
                    from pacbench.corpus.evaluation import source_evidence
                    from pacbench.judging.grade import (
                        Claim,
                        judge_prompt,
                        validate_diagnostic,
                    )
                    from pacbench.workflows.turn_evidence import diagnostic_inputs
                    observed_turn = TurnResult(**recall)
                    inputs = diagnostic_inputs(observed_turn, contract, source_evidence(messages, summary['evidence_diagnostic_protocol']), diagnostic_version=summary['evidence_diagnostic_protocol'])
                    claims = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
                    response = json.loads(judgment['response'])
                    if summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) != LEGACY_JUDGE_PROTOCOL:
                        from pacbench.judging.grade import audit_native_judge_attempts
                        audit_native_judge_attempts(arm/'judgment.json', question=judge_question(recall_turn), answer=recall['answer'], claims=claims,
                            judge_protocol=summary['judge_protocol'], assess_recovery=True, diagnostic_inputs=inputs,
                            diagnostic_version=summary['evidence_diagnostic_protocol'])
                    check(judgment.get('diagnostic_protocol') == summary['evidence_diagnostic_protocol'] and judgment.get('diagnostic_inputs') == inputs,
                        f'{label}: evidence diagnostic scope mismatch')
                    check(judgment.get('prompt') == judge_prompt(judge_question(recall_turn), recall['answer'], claims,
                        assess_recovery=True, diagnostic_inputs=inputs, diagnostic_version=summary['evidence_diagnostic_protocol'], judge_protocol=summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)) + judgment.get('prompt_suffix', ''), f'{label}: evidence diagnostic prompt mismatch')
                    check(grade.get('evidence_diagnostic') == validate_diagnostic(response.get('evidence_diagnostic'),
                        claims, recall['answer'], inputs, summary['evidence_diagnostic_protocol']), f'{label}: evidence diagnostic differs from judge')
                if plan:
                    check(json.loads(judgment['response']).get('recovery') == grade.get('recovery'), f'{label}: recovery eligibility differs from judge')
                    check(json.loads(judgment['response'])['verdicts'] == grade['claims'],
                          f'{label}: initial grade differs from recorded judge response')
                    errors.extend(f'{label}: {e}' for e in audit_followups(contract=contract, plan=plan,
                        initial_turn=TurnResult(**recall), initial_grade=grade, output=arm / 'followups',
                        provenance_version=summary.get('provenance_version', 'source-provenance-v1'),
                        evidence_by_id=source_evidence(messages, summary['evidence_diagnostic_protocol']) if summary.get('evidence_diagnostic_protocol') else None,
                        diagnostic_version=summary.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION),
                        judge_protocol=summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL),
                        clock_context_protocol=clock_protocol,prompt_protocol=prompt_protocol))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                errors.append(f'{label}: {type(exc).__name__}: {exc}')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            **({'selected_providers':sorted(selected)} if providers is not None else {}),
            'scope': 'native trace integrity, not calibrated provider quality'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--providers',nargs='+',help='Audit only these explicitly selected provider arms')
    args = parser.parse_args(argv)
    report = audit(args.run,providers=args.providers)
    print(json.dumps(report, indent=2))
    return int(bool(report['errors']))


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
            if report.get('provider_name'):state['provider_name']=report['provider_name']
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
            if (turn.hermes_gateway or {}).get('version')=='checkpoint-session-v1':
                from pacbench.workflows.turn_evidence import audit_transport
                audit_transport(turn)
            from pacbench.usage import provider_paths_match
            if (directory/'usage.json').exists():
                check(provider_paths_match(turn,read(directory/'usage.json')),
                    f"{row['id']}: provider path timing differs from recorded inference inputs")
            from pacbench.context_isolation import expected_prompt
            expected=expected_prompt(row['at'],contract.question,clock_context_protocol)
            if prompt_protocol is not None:expected=harness.question_prompt(contract.question,row['at'],prompt_protocol)
            check(turn.at==row['at'] and turn.prompt==expected,f"{row['id']}: evaluation question/clock mismatch")
            if report.get('strict_memory_protocol')=='strict-memory-only-v1':
                check((directory/'strict-memory.json').exists(),f"{row['id']}: missing strict provenance receipt")
            if (directory/'strict-memory.json').exists():
                from pacbench.source_policy import classify_retention as classify
                decision=classify(report.get('provider_name',root.name),turn,state.get('strict','eligible'))
                state['strict']=decision['state']
                check(read(directory/'strict-memory.json')==decision,f"{row['id']}: strict provenance mismatch")
                if decision['state']!='eligible':
                    check(item['state']=='strict-ungraded' and item['verdict'] is None,f"{row['id']}: excluded answer was scored")
                    check(not (directory/'grade.json').exists() and not (directory/'judgment.json').exists(),f"{row['id']}: excluded answer was judged")
                    advance(state,turn,directory,provenance_version)
                    continue
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
                    evidence_by_id=(evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id), diagnostic_version=diagnostic_version, judge_protocol=judge_protocol, clock_context_protocol=clock_context_protocol, prompt_protocol=prompt_protocol,
                    provider_name=report.get('provider_name',root.name),strict_memory=report.get('strict_memory_protocol')=='strict-memory-only-v1'))
                recovery=read(directory/'followups/recovery.json')
                check(item['recovery']['score']==recovery['score'] and item['recovery']['attempts']==recovery['attempts'] and
                    item['recovery'].get('score_state')==recovery.get('score_state'),f"{row['id']}: summary/score mismatch")
            advance(state,turn,directory,provenance_version)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:errors.append(f'evaluation audit: {exc}')
    return errors

if __name__ == '__main__':
    raise SystemExit(main())
