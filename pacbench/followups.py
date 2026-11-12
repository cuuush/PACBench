"""One natural recovery message for eligible omissions, with retrieval effort."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from . import harness
from .gateway_client import require_live_cost_boundary
from .model_calls import ModelOutput
from .stop import OperatorStop
from .semantic_grade import Claim, Grade, GradedClaim, VERDICTS, grade_native_semantic, audit_native_judge_attempts, LEGACY_JUDGE_PROTOCOL
from .clock_context import judge_question

RECOVERY_WEIGHT = 0.5
SOURCE_CATEGORIES = {'archive_search', 'source_evidence', 'source_messages'}


def save(path: Path, value) -> None:
    """Publish a complete checkpoint, even while the explorer reads the run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix='.'+path.name+'-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(value,stream,indent=2,ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_json(model, prompt, records=None):
    response = model(prompt)
    if records is not None:
        records.append({"model": response.model, "usage": response.usage, "record_path": response.record_path} if isinstance(response, ModelOutput) else {"model":None,"usage":None,"record_path":None})
    return json.loads(response.text if isinstance(response, ModelOutput) else response)


def freeze_policy(contract, path: Path) -> dict:
    """Bind the recovery policy before testing, without spending on unused questions."""
    if path.exists():
        return load_questions(path, contract)
    body = {'protocol_version': 3, 'contract_hash': contract.contract_hash,
            'authoring_revision': 'specific-missing-facts-v3',
            'minimum_initial_correct': 1,
            'eligibility_policy': 'natural-omissions-v1',
            'claim_ids': [c.id for c in contract.claims],
            'policy': 'one combined Spark-authored clarification for silent implicit omissions only'}
    result = {**body, 'plan_hash': digest(body)}
    save(path, result)
    return result


def load_questions(path: Path, contract) -> dict:
    plan = json.loads(path.read_text())
    body = {k: v for k, v in plan.items() if k != 'plan_hash'}
    if plan.get('plan_hash') != digest(body) or plan.get('contract_hash') != contract.contract_hash:
        raise ValueError('follow-up plan hash/contract mismatch')
    if (plan.get('protocol_version') != 3 or
            plan.get('authoring_revision') != 'specific-missing-facts-v3' or
            plan.get('minimum_initial_correct') != 1 or
            plan.get('eligibility_policy') != 'natural-omissions-v1' or
            plan.get('claim_ids') != [c.id for c in contract.claims]):
        raise ValueError('follow-up policy version, eligibility or claim IDs mismatch')
    return plan


def tool_category(name: str, arguments: dict, source_tools=()) -> str:
    """Classify documented tool operations, never infer semantic answer correctness."""
    namespace = name.split('__')[1] if name.startswith('mcp__') and len(name.split('__')) >= 3 else ''
    if (name in source_tools or name in {'session_search', 'imessage_search', 'search_messages', 'grep_messages', 'search_messages_temporal', 'get_messages_by_date_range'} or
            namespace.lower() in {'imessage', 'messages'}):
        return 'source_messages'
    if name == 'memcal_search_archive':
        return 'archive_search'
    if name in {'memcal_open_source', 'memcal_conversation', 'memcal_activity'}:
        return 'source_evidence'
    if name=='get_observation_context':return 'source_evidence'
    if name in {'honcho_search', 'honcho_context'}:
        return 'source_messages'
    if name == 'honcho_profile':
        return 'memory_detail'
    if name == 'honcho_reasoning':
        return 'memory_reasoning'
    if name == 'honcho_conclude':
        return 'memory_search' if arguments.get('list') else 'memory_write'
    if name == 'cognee_recall':
        mode=str(arguments.get('search_type') or '').upper()
        return 'source_evidence' if mode in {'CHUNKS','CHUNKS_LEXICAL','RAG_COMPLETION','HYBRID_COMPLETION'} else 'unclassified'
    if name == 'cognee_recap':
        return 'source_messages'  # It exposes original recorded session history.
    if name in {'cognee_remember','cognee_forget'}:
        return 'memory_write'
    if name == 'cognee_switch_dataset':
        return 'memory_detail'
    if name == 'cognee_code_search':
        return 'source_evidence'
    if name=='memos_search':return 'source_evidence'
    if name=='memos_timeline':return 'source_messages'
    if name=='memos_get':
        return 'source_evidence' if arguments.get('kind','trace')=='trace' else 'memory_detail'
    if name in {'memos_skill_list','memos_skill_get','memos_environment'}:
        return 'memory_detail'
    if name in {'tool_search', 'tool_describe'}:
        return 'tool_discovery'
    if name == 'tool_call':
        return 'tool_dispatch'
    if name in {'memcal_open', 'memcal_open_page', 'memory'}:
        return 'memory_detail'
    if name in {'memcal_search_wiki', 'memcal_list_days', 'memcal_list_month',
                'mem0_search', 'hindsight_recall', 'mcp__hindsight__recall', 'mcp__hindsight__list_memories'}:
        return 'memory_search'
    if name in {'supermemory-search','supermemory_search','brv_query'}:
        return 'memory_search'
    if name in {'supermemory-profile','supermemory_profile','brv_status'}:
        return 'memory_detail'
    if name in {'supermemory-save','supermemory_store','supermemory-forget','supermemory_forget','brv_curate'}:
        return 'memory_write'
    if name=='hindsight_reflect':
        return 'memory_reasoning'
    if name in {'viking_search','viking_browse'}:
        return 'memory_search'
    if name=='viking_read':
        from .source_policy import SOURCE_ARGUMENT_RULES, source_read_blocked
        if source_read_blocked(name,arguments,SOURCE_ARGUMENT_RULES):
            return 'source_evidence'
        return 'memory_detail'
    if name in {'viking_add_resource','viking_remember','viking_forget'}:
        return 'memory_write'
    if name == 'fact_store':
        if arguments.get('action') in {'add', 'store', 'update', 'delete'}:
            return 'memory_write'
        return 'memory_search' if arguments.get('action') in {'list', 'search'} else 'memory_detail'
    if name in {'mem0_add', 'mem0_update', 'mem0_delete', 'fact_feedback','hindsight_retain',
                'memcal_add', 'memcal_update', 'memcal_note', 'memcal_reviewed'}:
        return 'memory_write'
    # New providers/connectors need a reviewed operation mapping; unknown is not cheap/free.
    return 'unclassified'


def tool_profile(turn, *, source_tools=(), provenance_version='source-provenance-v1') -> dict:
    if provenance_version not in {'source-provenance-v1', 'source-provenance-v2', 'source-provenance-v3'}:
        raise ValueError('Unknown source provenance version')
    operations = []
    for index, call in enumerate(turn.tool_calls):
        name = call.get('name', '')
        arguments = call.get('input', call.get('arguments', {}))
        if not isinstance(arguments, dict):
            arguments = {}
        result = turn.tool_results[index] if index < len(turn.tool_results) else {}
        category = tool_category(name, arguments, source_tools)
        if name=='honcho_context' and name not in source_tools:
            from .source_policy import proven_honcho_snapshot
            if proven_honcho_snapshot(result):category='memory_detail'
        if name == 'viking_read' and category == 'source_evidence' and name not in source_tools:
            from .source_policy import proven_viking_summary
            if proven_viking_summary(arguments, result):
                category = 'memory_detail'
        operations.append({'name': name, 'arguments': arguments,
            'category': category, 'dispatch_parent': None,
            'success': result.get('success'), 'policy_blocked': result.get('policy_blocked'),
            'timestamp': call.get('timestamp'), 'result': result})
        if name == 'tool_call':
            try:
                payload = json.loads(result.get('text', '{}'))
            except (ValueError, TypeError):
                payload = {}
            outcomes = payload.get('results') if isinstance(payload, dict) else None
            entries = arguments.get('calls', [])
            for nested_index, entry in enumerate(entries if isinstance(entries, list) else []):
                if not isinstance(entry, dict):
                    continue
                nested_name = entry.get('name', '')
                nested_args = entry.get('arguments', {})
                nested_result = (outcomes[nested_index] if isinstance(outcomes, list) and
                                 nested_index < len(outcomes) else payload)
                operations.append({'name': nested_name, 'arguments': nested_args,
                    'category': tool_category(nested_name, nested_args, source_tools),
                    'dispatch_parent': index, 'result': nested_result,
                    'success': (False if isinstance(nested_result, dict) and 'error' in nested_result
                                else result.get('success')),
                    'policy_blocked': result.get('policy_blocked')})
    categories = dict(Counter(op['category'] for op in operations))
    signatures = Counter((op['name'], json.dumps(op['arguments'], sort_keys=True)) for op in operations)
    traces = getattr(turn, 'provider_traces', []) or []
    native_calls = [call for trace in traces for call in trace.get('record', {}).get('toolCalls', [])]
    native_operations=[]
    for trace in traces:
        if trace.get('provider') not in {'honcho','hindsight'}:continue
        for call in trace.get('record',{}).get('toolCalls',[]):
            name=call.get('toolName','')
            category=('memory_search' if name=='search_memory' else
                      'memory_detail' if name in {'get_reasoning_chain','get_peer_card'} else
                      tool_category(name,call.get('args',{}),source_tools))
            if trace.get('provider')=='hindsight':
                from .hindsight_traces import category as hindsight_category
                category=hindsight_category(call, provenance_version=provenance_version)
            native_operations.append({**call,'category':category,'attribution':trace.get('attribution'),
                **({'path_attribution':trace['path_attribution']} if 'path_attribution' in trace else {})})
    answer_operations=[op for op in native_operations if op.get('path_attribution',{}).get('affects_answer') is not False]
    native_source=any(op['category'] in SOURCE_CATEGORIES and op['attribution']=='explicit-query-match'
                      for op in answer_operations)
    native_unknown=sum(bool(t.get('error')) for t in traces if t.get('provider') in {'honcho','hindsight'})+sum(op['category']=='unclassified' or
        op['category'] in SOURCE_CATEGORIES and op['attribution']!='explicit-query-match' for op in answer_operations)
    prefetch_provenance=None
    if provenance_version=='source-provenance-v3':
        from .prefetch_provenance import observe
        declared=getattr(turn,'prefetch_provenance',None)
        if declared:
            prefetch_provenance=observe({k:v for k,v in declared.items() if k!='prefetch_sha256'},turn.prefetch_context)
            if prefetch_provenance!=declared:
                raise ValueError('Native prefetch provenance differs from actual context')
            native_source=native_source or declared['state']=='source'
            native_unknown+=declared['state']=='unverified'
    return {'top_level_calls': len(turn.tool_calls), 'total_tool_operations': len(operations),
        'provider_internal_tool_calls': len(native_calls) if traces and all(
            'toolCalls' in trace.get('record', {}) for trace in traces) else None,
        'provider_task_records': len(traces),
        'provider_internal_operations': native_operations,
        'provider_internal_source_fallback_confirmed': native_source,
        'provider_internal_path_needs_review': bool(native_unknown),
        'categories': categories, 'operations': operations,
        'failed_operations': sum(op.get('success') is False for op in operations),
        'repeated_operations': sum(count - 1 for count in signatures.values()),
        'unmatched_tool_results': turn.tool_results[len(turn.tool_calls):],
        'source_fallback_attempted': native_source or any(c in SOURCE_CATEGORIES for c in categories),
        'unclassified_operations': categories.get('unclassified', 0)+native_unknown,
        **({'prefetch_provenance':prefetch_provenance} if provenance_version=='source-provenance-v3' else {})}


def turn_metrics(turn, previous=None, *, source_tools=(), provenance_version='source-provenance-v1') -> dict:
    def delta(field):
        current = getattr(turn, field)
        prior = getattr(previous, field) if previous is not None else 0
        if previous is not None and previous.session_id != turn.session_id:
            return None
        return current - prior if current is not None and prior is not None and current >= prior else None
    return {**tool_profile(turn, source_tools=source_tools, provenance_version=provenance_version), 'latency_s': turn.latency_s,
        'input_tokens': delta('session_input_tokens'), 'output_tokens': delta('session_output_tokens'),
        'policy_blocks': turn.policy_blocks, 'returncode': turn.returncode}


def initial_effort(turn, previous=None, *, source_tools=(), inherited_path=None, provenance_version='source-provenance-v1'):
    metrics = turn_metrics(turn, previous, source_tools=source_tools, provenance_version=provenance_version)
    if inherited_path is not None:
        metrics.update(inherited_source_fallback=bool(inherited_path.get('source_fallback')),
                       inherited_unclassified=bool(inherited_path.get('unclassified')))
    return metrics


def cost_window(before: dict, after: dict) -> dict:
    prior = {p['project']: p for p in before.get('projects', [])}
    fields = ('calls', 'settled_upper_bound_usd', 'held_usd', 'input_tokens', 'output_tokens')
    def delta(p, field):
        current = p.get(field)
        previous = prior[p['project']].get(field) if p['project'] in prior else 0
        return current - previous if isinstance(current, (int, float)) and isinstance(previous, (int, float)) else None
    return {'projects': [{ 'project': p['project'], **{
        field + '_delta': delta(p, field)
        for field in fields}} for p in after.get('projects', [])
        if any(p.get(f, 0) != prior.get(p['project'], {}).get(f, 0) for f in fields)],
        'scope': 'gateway project activity during this turn; asynchronous work may settle later; not tool prices'}


def recovery_score(grade, initial_metrics: dict, attempts: list[dict]) -> dict:
    by_id = {}
    for attempt in attempts:
        verdicts = {r['id']: r['verdict'] for r in attempt.get('verdicts', [])}
        for claim_id in attempt['claim_ids']:
            by_id[claim_id] = {**attempt, 'verdict': verdicts.get(claim_id)}
    points = []
    for slot in grade.slots:
        attempt = by_id.get(slot.id)
        initial_failure = initial_metrics['source_fallback_attempted'] or initial_metrics.get('inherited_source_fallback', False)
        unknown = bool(initial_metrics['unclassified_operations'] or initial_metrics.get('inherited_unclassified', False))
        failed_retention = initial_failure or bool(attempt and attempt['metrics']['source_fallback_attempted'])
        unknown = unknown or bool(attempt and attempt['metrics']['unclassified_operations'])
        recovered = bool(attempt and attempt.get('verdict') == 'correct')
        earned = (1.0 if slot.verdict == 'correct' else RECOVERY_WEIGHT if recovered else 0.0)
        if failed_retention or unknown or attempt and attempt['state'] != 'graded':
            earned = 0.0
        points.append({'claim_id': slot.id, 'initial_verdict': slot.verdict,
            'followup_verdict': attempt.get('verdict') if attempt else None,
            'recovered_answer': recovered, 'retention_failed': failed_retention,
            'path_needs_review': unknown, 'points': earned})
    return {'initial_correct': grade.correct, 'initial_missed': grade.missed,
        'initial_misrepresented': grade.misrepresented, 'max_points': len(grade.slots),
        'points': sum(p['points'] for p in points), 'recovery_weight': RECOVERY_WEIGHT,
        'claims': points, 'calibrated': False}


def _covers_all(review, claim_ids):
    covered = review.get('covers_claim_ids')
    return (isinstance(covered, list) and all(isinstance(c, str) for c in covered)
            and len(covered) == len(claim_ids) and set(covered) == set(claim_ids))


def combined_binding(contract, component_plan, selected):
    return {'contract_hash':contract.contract_hash,'component_plan_hash':component_plan['plan_hash'],
            'claim_ids':[c.id for c in selected], 'authoring_version':'scoped-recovery-v3'}


def combined_plan(contract, component_plan: dict, selected: list, model, directory: Path, *, known_context=(), cache_state=None) -> dict:
    """Share one reviewed wording across concurrent arms without duplicate calls."""
    import fcntl
    binding=combined_binding(contract,component_plan,selected)
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/(digest(binding)+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if cache_state is not None:cache_state['reused']=(directory/(digest(binding)+'.json')).exists()
        return _combined_plan(contract,component_plan,selected,model,directory,known_context=known_context)


def _combined_plan(contract, component_plan: dict, selected: list, model, directory: Path, *, known_context=()) -> dict:
    """Spark authors one message for an observed missing-fact set; reuse it across arms."""
    claim_ids = [c.id for c in selected]
    binding = combined_binding(contract, component_plan, selected)
    path = directory / (digest(binding) + '.json')
    if path.exists():
        return load_combined_plan(path, binding)
    authoring_calls = []
    hidden = [{'claim_id': c.id, 'claim': c.statement} for c in selected]
    write_prompt = (
        'Write one short user message asking for each missing detail. Ask for its value, '
        'preserving current versus historical meaning. Do not supply answers or tool instructions. '
        'Use the actual answer to identify the event; avoid private ordinals like first outing. '
        'Return ONLY JSON {"question":"message"}.\n'
        f'Original question: {contract.question}\nActual answer: {json.dumps(known_context)}\nMissing facts: {json.dumps(hidden)}')
    review_prompt = (
        'Does this message naturally ask for all missing information without stating its answers, '
        'supplying a missing name/time/place as a confirmation, or prescribing tools? '
        'Object-specific why/how/what-happened questions are valid. A yes/no question about a '
        'boolean status does not assert that status is true. Preserve the missing facts\' own '
        'current/historical meaning, including historical reasons for a current state. '
        'Reject ambiguous event anchors; details actually in the answer may identify the event, missing values may not. '
        'Return ONLY JSON {"safe":true,"covers_claim_ids":["covered IDs"],"reason":"explanation"}.\n'
        f'Original question: {contract.question}\nActual answer: {json.dumps(known_context)}\nMissing facts: {json.dumps(hidden)}\n')
    rejected = []
    for _ in range(2):
        feedback = ('\nRevise the rejected draft using this independent review: '
                    + json.dumps(rejected[-1])) if rejected else ''
        authored = model_json(model, write_prompt + feedback, records=authoring_calls)
        question = authored['question']
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError('combined follow-up must be one bounded message')
        review = model_json(model, review_prompt + f'Proposed message: {question}',
                            records=authoring_calls)
        if (review.get('safe') is True and _covers_all(review, claim_ids) and
                isinstance(review.get('reason'), str) and review['reason'].strip()):
            break
        rejected.append({'question': question, 'review': review})
        save(path.with_suffix('.rejected.json'), {**rejected[-1], 'drafts': rejected})
    else:
        raise ValueError('combined follow-up failed neutral/complete coverage review')
    origin = 'spark-combined-message'
    body = {**binding, 'known_context':list(known_context),'context_source':'actual-initial-answer',
            'question': question, 'review': review, 'origin': origin, 'authoring_calls': authoring_calls}
    result = {**body, 'question_hash': digest(body)}
    save(path, result)
    return result


def load_combined_plan(path: Path, binding: dict) -> dict:
    plan = json.loads(path.read_text())
    body = {k: v for k, v in plan.items() if k != 'question_hash'}
    if (plan.get('question_hash') != digest(body) or
            any(plan.get(k) != v for k, v in binding.items()) or
            not isinstance(plan.get('question'), str) or not 1 <= len(plan['question'].strip()) <= 2000 or
            plan.get('review', {}).get('safe') is not True or
            not _covers_all(plan.get('review', {}), binding['claim_ids'])):
        raise ValueError('combined follow-up hash/coverage/contract mismatch')
    return plan


def natural_eligibility(grade):
    if grade.correct == 0:
        return [], 'no_initial_correct_facts'
    if grade.misrepresented:
        return [], 'incorrect_answer'
    if not grade.missed:
        return [], None
    from .semantic_grade import validate_recovery
    validate_recovery(grade.recovery, [vars(s) for s in grade.slots])
    kinds = {r['kind'] for r in grade.recovery}
    if 'unavailable' in kinds:
        return [], 'admitted_retrieval_failure'
    if 'explicitly_requested' in kinds:
        return [], 'explicit_request_failed'
    return [r['id'] for r in grade.recovery if r['kind'] == 'implicit_omission'], None


def run_followups(*, contract, plan: dict, grade, initial_turn, output: Path,
                  judge, home: Path, allowlist: str, extra_env: dict | None,
                  source_tools=(), run_turn=None, budget_snapshot=None,
                  question_model=None, question_dir: Path | None = None,
                  previous_turn=None, conversation=(), inherited_path=None, provenance_version='source-provenance-v1', evidence_by_id=None, diagnostic_version='answer-visible-evidence-v1', judge_protocol=LEGACY_JUDGE_PROTOCOL) -> dict:
    """Ask once for all initially failed claims in the real recall conversation."""
    run_turn = run_turn or harness.run_turn
    budget_snapshot = budget_snapshot or require_live_cost_boundary
    attempts = []
    initial_metrics = initial_effort(initial_turn, previous_turn, source_tools=source_tools, inherited_path=inherited_path, provenance_version=provenance_version)
    report = {'state': 'running', 'plan_hash': plan['plan_hash'], 'attempts': attempts,
              'protocol_version': 5, 'minimum_initial_correct': 1, 'initial_metrics': initial_metrics,
              'policy': 'one combined follow-up only for implicit omissions; explicit failures, unknowns and wrong answers are final'}
    output.mkdir(parents=True, exist_ok=True)
    # A restarted caller cannot ask the same questions again or replace a failed attempt.
    with (output / 'recovery.json').open('x') as record:
        record.write(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    selected_ids, skip_reason = natural_eligibility(grade)
    report['eligibility'] = grade.recovery
    report['eligible_claim_ids'] = selected_ids
    if skip_reason:
        report['skip_reason'] = skip_reason
    selected = [c for c in contract.claims if c.id in selected_ids]
    if selected:
        directory = output / 'combined'
        attempt = {'claim_ids': [c.id for c in selected], 'question': None, 'state': 'failed',
                   'verdicts': [], 'metrics': {'source_fallback_attempted': False, 'unclassified_operations': 0}}
        attempts.append(attempt)
        # Save planned attempt first, so a crash cannot silently become an unattempted fact.
        save(output / 'recovery.json', report)
        try:
            if not initial_turn.session_id:
                raise ValueError('cannot follow up without the real recall session')
            budget_snapshot()
            question_root = question_dir or output / 'question-plans'
            # A hidden claim's scope may contain another omitted value. Only
            # the public answer is safe anchoring context for question writing.
            known_context = [initial_turn.answer]
            reuse_state={}
            authored = combined_plan(contract, plan, selected, question_model or judge, question_root,
                                     known_context=known_context,cache_state=reuse_state)
            attempt['question_authoring_reused']=reuse_state['reused']
            question = authored['question']
            attempt.update(question=question, question_hash=authored['question_hash'])
            save(directory / 'question-plan.json', authored)
            before = budget_snapshot()
            from .usage import tracked_turn
            turn_environment = extra_env
            if (home.parent/'provider-clock.json').exists():
                turn_environment = {**(extra_env or {}), 'PACBENCH_CLOCK_RECOVERY':'1'}
            turn = tracked_turn(run_turn, question, usage_path=directory / "usage.json", at=initial_turn.at, home=home, allowlist=allowlist,
                            extra_env=turn_environment, timeout_s=180, session_id=initial_turn.session_id,
                            session_started_at=initial_turn.session_started_at or initial_turn.at)
            save(directory / 'turn.json', vars(turn))
            attempt['metrics'] = turn_metrics(turn, initial_turn, source_tools=source_tools, provenance_version=provenance_version)
            attempt['gateway_window'] = cost_window(before, budget_snapshot())
            if turn.session_id != initial_turn.session_id or turn.returncode or turn.policy_blocks or not turn.answer.strip():
                raise ValueError('follow-up turn failed, changed session, or was policy blocked')
            from .native_checks import diagnostic_inputs
            from types import SimpleNamespace
            inputs = diagnostic_inputs(turn, SimpleNamespace(claims=selected), evidence_by_id, prior_turn=initial_turn, diagnostic_version=diagnostic_version) if evidence_by_id is not None else None
            followup_grade = grade_native_semantic(grade.checkpoint, judge_question(turn), turn.answer,
                tuple(Claim(c.id, c.statement, c.evidence) for c in selected), judge,
                record_path=directory / 'judgment.json', conversation=tuple(conversation) + (
                    {'role': 'user', 'content': judge_question(initial_turn)},
                    {'role': 'assistant', 'content': initial_turn.answer}), diagnostic_inputs=inputs, diagnostic_version=diagnostic_version, judge_protocol=judge_protocol)
            save(directory / 'grade.json', {'claims': [vars(s) for s in followup_grade.slots],
                **({'evidence_diagnostic': followup_grade.evidence_diagnostic} if inputs is not None else {})})
            attempt.update(state='graded', verdicts=[vars(s) for s in followup_grade.slots])
        except OperatorStop:
            attempt['state'] = 'interrupted'
            report['state'] = 'interrupted'
            save(output / 'recovery.json', report)
            raise
        except Exception as exc:
            attempt['error'] = f'{type(exc).__name__}: {exc}'
            report['state'] = 'incomplete'
        report['score'] = recovery_score(grade, initial_metrics, attempts)
        save(output / 'recovery.json', report)
    report['score'] = recovery_score(grade, initial_metrics, attempts)
    if report['state'] == 'running':
        report['state'] = 'complete'
    save(output / 'recovery.json', report)
    return report


def audit_followups(*, contract, plan: dict, initial_turn, initial_grade: dict,
                    output: Path, previous_turn=None, conversation=(), inherited_path=None, provenance_version='source-provenance-v1', evidence_by_id=None, diagnostic_version='answer-visible-evidence-v1', judge_protocol=LEGACY_JUDGE_PROTOCOL, clock_context_protocol='user-clock-prefix-v1', prompt_protocol=None) -> list[str]:
    """Recheck attempt limits, unchanged initial score, real session and trace costs."""
    errors = []
    def check(ok, message):
        if not ok:
            errors.append(message)
    def read(path):
        return json.loads(path.read_text())
    try:
        report = read(output / 'recovery.json')
        check(report['state'] == 'complete', 'follow-up diagnostics incomplete')
        check(report['plan_hash'] == plan['plan_hash'], 'follow-up plan mismatch')
        ids = [c.id for c in contract.claims]
        rows = initial_grade['claims']
        check([r['id'] for r in rows] == ids, 'initial claim order mismatch')
        check(all(r['verdict'] in VERDICTS for r in rows) and
              all(initial_grade[v] == sum(r['verdict'] == v for r in rows)
                  for v in ('correct', 'missed', 'misrepresented')), 'initial score counts mismatch')
        grade = Grade(checkpoint='audit', slots=[GradedClaim(**r) for r in rows],
                      correct=initial_grade['correct'], missed=initial_grade['missed'],
                      misrepresented=initial_grade['misrepresented'])
        check(report.get('protocol_version') == 5, 'unsupported recovery protocol')
        check(report.get('minimum_initial_correct') == 1, 'follow-up eligibility threshold mismatch')
        grade.recovery = initial_grade.get('recovery')
        failed, skip = natural_eligibility(grade)
        eligible = bool(failed)
        check(report.get('eligibility') == grade.recovery and report.get('eligible_claim_ids') == failed,
              'recovery eligibility mismatch')
        check(report.get('skip_reason') == skip, 'follow-up skip reason mismatch')
        check(len(report['attempts']) == int(eligible) and
              (not eligible or report['attempts'][0]['claim_ids'] == failed),
              'expected exactly one combined follow-up covering all eligible omissions')
        check(len(list(output.glob('*/turn.json'))) == int(eligible), 'unexpected combined follow-up turn count')
        effort = initial_effort(initial_turn, previous_turn, inherited_path=inherited_path, provenance_version=provenance_version)
        check(report['initial_metrics'] == effort, 'initial effort mismatch')
        previous = initial_turn
        for attempt in report['attempts']:
            directory = output / 'combined'
            selected_ids = attempt['claim_ids']
            frozen_question = load_combined_plan(directory / 'question-plan.json', {
                'contract_hash': contract.contract_hash, 'component_plan_hash': plan['plan_hash'],
                'claim_ids': failed})
            if frozen_question.get('authoring_version') == 'scoped-recovery-v2':
                known = [c.statement for c in contract.claims if next(s for s in grade.slots if s.id == c.id).verdict == 'correct']
                load_combined_plan(directory / 'question-plan.json',{
                    'contract_hash':contract.contract_hash,'component_plan_hash':plan['plan_hash'],
                    'claim_ids':failed,'authoring_version':'scoped-recovery-v2','known_context':known})
            elif frozen_question.get('authoring_version') == 'scoped-recovery-v3':
                load_combined_plan(directory/'question-plan.json',
                    combined_binding(contract,plan,[c for c in contract.claims if c.id in failed]))
                check(frozen_question.get('context_source')=='actual-initial-answer', 'follow-up used hidden anchoring context')
                if not attempt.get('question_authoring_reused'):
                    check(frozen_question.get('known_context')==[initial_turn.answer], 'follow-up context differs from actual answer')
            check(attempt['question_hash'] == frozen_question['question_hash'], 'combined question hash mismatch')
            question = frozen_question['question']
            check(attempt['question'] == question, 'follow-up question changed')
            turn = harness.TurnResult(**read(directory / 'turn.json'))
            from .usage import provider_paths_match
            if (directory/'usage.json').exists():
                check(provider_paths_match(turn,read(directory/'usage.json')),
                    'follow-up provider path timing differs from recorded inference inputs')
            clock = turn.provider_clock
            recovery_at = turn.at if clock and clock.get('version') in ('honcho-wall-clock-v2','honcho-wall-clock-v3') else initial_turn.at
            if clock:
                from datetime import datetime
                check(clock.get('role') == 'recovery' and clock.get('requested_at') == initial_turn.at and
                      datetime.fromisoformat(turn.at) >= datetime.fromisoformat(initial_turn.at),
                      'invalid simulated recovery clock')
            from .clock_context import expected_prompt
            expected=expected_prompt(recovery_at,question,clock_context_protocol)
            if prompt_protocol is not None:expected=harness.question_prompt(question,recovery_at,prompt_protocol)
            check(turn.prompt == expected and turn.at == recovery_at, 'follow-up clock/prompt mismatch')
            check(turn.session_id == initial_turn.session_id, 'follow-up lost recall conversation')
            check(not turn.returncode and not turn.policy_blocks, 'follow-up turn failed/blocked')
            check(attempt['metrics'] == turn_metrics(turn, previous, provenance_version=provenance_version), 'follow-up tool/token metrics mismatch')
            judgment = read(directory / 'judgment.json')
            check(judgment.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) == judge_protocol, 'follow-up judge protocol mismatch')
            check(judgment['question'] == judge_question(turn) and judgment['answer'] == turn.answer,
                  'follow-up judgment context mismatch')
            if 'conversation' in judgment:
                check(judgment['conversation'] == list(conversation) + [
                    {'role': 'user', 'content': judge_question(initial_turn)},
                    {'role': 'assistant', 'content': initial_turn.answer}], 'follow-up prior conversation mismatch')
            selected = [c for c in contract.claims if c.id in selected_ids]
            if judge_protocol != LEGACY_JUDGE_PROTOCOL:
                from .native_checks import diagnostic_inputs
                from types import SimpleNamespace
                inputs = diagnostic_inputs(turn, SimpleNamespace(claims=selected), evidence_by_id, prior_turn=initial_turn, diagnostic_version=diagnostic_version) if evidence_by_id is not None else None
                audit_native_judge_attempts(directory/'judgment.json', question=judge_question(turn), answer=turn.answer,
                    claims=tuple(Claim(c.id, c.statement, c.evidence) for c in selected), judge_protocol=judge_protocol,
                    conversation=list(conversation) + [{'role':'user','content':judge_question(initial_turn)}, {'role':'assistant','content':initial_turn.answer}],
                    diagnostic_inputs=inputs, diagnostic_version=diagnostic_version)
            judgments = read(directory / 'grade.json')['claims']
            check(json.loads(judgment['response'])['verdicts'] == judgments,
                  'follow-up grade differs from recorded judge response')
            if evidence_by_id is not None:
                check('diagnostic_protocol' in judgment and 'evidence_diagnostic' in read(directory / 'grade.json'), 'missing follow-up evidence diagnostic')
            if 'diagnostic_protocol' in judgment:
                from .native_checks import diagnostic_inputs
                from .semantic_grade import validate_diagnostic, DIAGNOSTIC_VERSIONS, judge_prompt
                from types import SimpleNamespace
                inputs = judgment.get('diagnostic_inputs', {})
                claim_objects = tuple(Claim(c.id, c.statement, c.evidence) for c in selected)
                saved_grade = read(directory / 'grade.json')
                check(diagnostic_version in DIAGNOSTIC_VERSIONS and judgment['diagnostic_protocol'] == diagnostic_version and
                    saved_grade.get('evidence_diagnostic') == validate_diagnostic(json.loads(judgment['response']).get('evidence_diagnostic'), claim_objects, turn.answer, inputs, diagnostic_version),
                    'follow-up diagnostic differs from judge')
                check(judgment['prompt'] == judge_prompt(judge_question(turn), turn.answer, claim_objects,
                    conversation=judgment['conversation'], diagnostic_inputs=inputs, diagnostic_version=diagnostic_version, judge_protocol=judge_protocol) + judgment.get('prompt_suffix', ''), 'follow-up diagnostic prompt mismatch')
                reconstructed = diagnostic_inputs(turn, SimpleNamespace(claims=selected),
                    evidence_by_id if evidence_by_id is not None else {x['id']: x['text'] for x in inputs.get('source_evidence', [])}, prior_turn=initial_turn, diagnostic_version=diagnostic_version)
                check(inputs.get('answer_visible') == reconstructed['answer_visible'] and
                    (evidence_by_id is None or inputs == reconstructed), 'follow-up diagnostic evidence mismatch')
            check(judgment['claims'] == [{'id': c.id, 'description': c.statement,
                                         'evidence': list(c.evidence)} for c in selected],
                  'follow-up judge received a different claim')
            check([r['id'] for r in judgments] == selected_ids and
                  all(r['verdict'] in VERDICTS for r in judgments) and
                  judgments == attempt['verdicts'],
                  'follow-up claim judgment mismatch')
            check(attempt['state'] == 'graded', 'follow-up ungraded')
            previous = turn
        check(report['score'] == recovery_score(grade, effort, report['attempts']),
              'recovery score mismatch or first-answer penalty erased')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append(f'follow-up audit failed: {type(exc).__name__}: {exc}')
    return errors
