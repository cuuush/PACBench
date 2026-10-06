"""One combined neutral recovery message for all failed facts, with retrieval effort."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from . import harness
from .gateway_client import require_live_cost_boundary
from .model_calls import ModelOutput
from .semantic_grade import Claim, Grade, GradedClaim, VERDICTS, grade_semantic

RECOVERY_WEIGHT = 0.5
SOURCE_CATEGORIES = {'archive_search', 'source_evidence', 'source_messages'}


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_json(model, prompt):
    response = model(prompt)
    return json.loads(response.text if isinstance(response, ModelOutput) else response)


def freeze_policy(contract, path: Path) -> dict:
    """Bind the recovery policy before testing, without spending on unused questions."""
    if path.exists():
        return load_questions(path, contract)
    body = {'protocol_version': 3, 'contract_hash': contract.contract_hash,
            'authoring_revision': 'current-vs-historical-v1',
            'claim_ids': [c.id for c in contract.claims],
            'policy': 'one combined Spark-authored message for all failed facts'}
    result = {**body, 'plan_hash': digest(body)}
    save(path, result)
    return result


def validate_questions(questions, claim_ids):
    if (not isinstance(questions, list) or len(questions) != len(claim_ids) or
            any(not isinstance(q, dict) or set(q) != {'claim_id', 'question'} or
                q.get('claim_id') != cid or not isinstance(q.get('question'), str) or
                not 1 <= len(q['question'].strip()) <= 500
                for q, cid in zip(questions, claim_ids))):
        raise ValueError('follow-up plan must contain one bounded question per claim')


def load_questions(path: Path, contract) -> dict:
    plan = json.loads(path.read_text())
    body = {k: v for k, v in plan.items() if k != 'plan_hash'}
    if plan.get('plan_hash') != digest(body) or plan.get('contract_hash') != contract.contract_hash:
        raise ValueError('follow-up plan hash/contract mismatch')
    if plan.get('protocol_version') == 3:
        if plan.get('claim_ids') != [c.id for c in contract.claims]:
            raise ValueError('follow-up policy claim IDs mismatch')
        return plan
    validate_questions(plan.get('questions'), [c.id for c in contract.claims])
    reviews = plan.get('reviews')
    if (not isinstance(reviews, list) or len(reviews) != len(contract.claims) or
            any(not isinstance(r, dict) or r.get('claim_id') != c.id or r.get('safe') is not True
                for r, c in zip(reviews, contract.claims))):
        raise ValueError('follow-up plan lacks successful neutral-question review')
    return plan


def tool_category(name: str, arguments: dict, source_tools=()) -> str:
    """Classify documented tool operations, never infer semantic answer correctness."""
    namespace = name.split('__')[1] if name.startswith('mcp__') and len(name.split('__')) >= 3 else ''
    if (name in source_tools or name in {'session_search', 'imessage_search', 'search_messages'} or
            namespace.lower() in {'imessage', 'messages'}):
        return 'source_messages'
    if name == 'memcal_search_archive':
        return 'archive_search'
    if name in {'memcal_open_source', 'memcal_conversation'}:
        return 'source_evidence'
    if name in {'tool_search', 'tool_describe'}:
        return 'tool_discovery'
    if name == 'tool_call':
        return 'tool_dispatch'
    if name in {'memcal_open', 'memcal_open_page', 'memory'}:
        return 'memory_detail'
    if name in {'memcal_search_wiki', 'memcal_list_days', 'memcal_list_month',
                'mem0_search', 'mcp__hindsight__recall', 'mcp__hindsight__list_memories'}:
        return 'memory_search'
    if name == 'fact_store':
        if arguments.get('action') in {'add', 'store', 'update', 'delete'}:
            return 'memory_write'
        return 'memory_search' if arguments.get('action') in {'list', 'search'} else 'memory_detail'
    if name in {'mem0_add', 'mem0_update', 'mem0_delete', 'fact_feedback',
                'memcal_add', 'memcal_update', 'memcal_note', 'memcal_reviewed'}:
        return 'memory_write'
    # New providers/connectors need a reviewed operation mapping; unknown is not cheap/free.
    return 'unclassified'


def tool_profile(turn, *, source_tools=()) -> dict:
    operations = []
    for index, call in enumerate(turn.tool_calls):
        name = call.get('name', '')
        arguments = call.get('input', call.get('arguments', {}))
        if not isinstance(arguments, dict):
            arguments = {}
        result = turn.tool_results[index] if index < len(turn.tool_results) else {}
        operations.append({'name': name, 'arguments': arguments,
            'category': tool_category(name, arguments, source_tools), 'dispatch_parent': None,
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
    return {'top_level_calls': len(turn.tool_calls), 'total_tool_operations': len(operations),
        'categories': categories, 'operations': operations,
        'failed_operations': sum(op.get('success') is False for op in operations),
        'repeated_operations': sum(count - 1 for count in signatures.values()),
        'unmatched_tool_results': turn.tool_results[len(turn.tool_calls):],
        'source_fallback_attempted': any(c in SOURCE_CATEGORIES for c in categories),
        'unclassified_operations': categories.get('unclassified', 0)}


def turn_metrics(turn, previous=None, *, source_tools=()) -> dict:
    def delta(field):
        current = getattr(turn, field)
        prior = getattr(previous, field) if previous is not None else 0
        if previous is not None and previous.session_id != turn.session_id:
            return None
        return current - prior if current is not None and prior is not None and current >= prior else None
    return {**tool_profile(turn, source_tools=source_tools), 'latency_s': turn.latency_s,
        'input_tokens': delta('session_input_tokens'), 'output_tokens': delta('session_output_tokens'),
        'policy_blocks': turn.policy_blocks, 'returncode': turn.returncode}


def cost_window(before: dict, after: dict) -> dict:
    prior = {p['project']: p for p in before.get('projects', [])}
    fields = ('calls', 'settled_upper_bound_usd', 'held_usd')
    return {'projects': [{ 'project': p['project'], **{
        field + '_delta': p.get(field, 0) - prior.get(p['project'], {}).get(field, 0)
        for field in fields}} for p in after.get('projects', [])
        if any(p.get(f, 0) != prior.get(p['project'], {}).get(f, 0) for f in fields)],
        'scope': 'gateway project activity during this turn; asynchronous work may settle later; not tool prices'}


def recovery_score(grade, initial_metrics: dict, attempts: list[dict]) -> dict:
    by_id = {}
    for attempt in attempts:
        if 'claim_ids' in attempt:
            verdicts = {r['id']: r['verdict'] for r in attempt.get('verdicts', [])}
            for claim_id in attempt['claim_ids']:
                by_id[claim_id] = {**attempt, 'verdict': verdicts.get(claim_id)}
        else:
            by_id[attempt['claim_id']] = attempt  # saved protocol-1 trials
    points = []
    for slot in grade.slots:
        attempt = by_id.get(slot.id)
        initial_failure = initial_metrics['source_fallback_attempted']
        unknown = bool(initial_metrics['unclassified_operations'])
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


def combined_plan(contract, component_plan: dict, selected: list, model, directory: Path) -> dict:
    """Spark authors one message for an observed missing-fact set; reuse it across arms."""
    claim_ids = [c.id for c in selected]
    binding = {'contract_hash': contract.contract_hash, 'component_plan_hash': component_plan['plan_hash'],
               'claim_ids': claim_ids}
    path = directory / (digest(binding) + '.json')
    if path.exists():
        return load_combined_plan(path, binding)
    components = [q for q in component_plan.get('questions', []) if q['claim_id'] in claim_ids]
    if len(selected) == 1 and components:
        question = components[0]['question']  # already authored and independently reviewed by Spark
        review = {'safe': True, 'covers_claim_ids': claim_ids, 'reason': 'Uses the frozen reviewed single-fact question.'}
        origin = 'frozen-spark-component'
    else:
        hidden = [{'claim_id': c.id, 'claim': c.statement} for c in selected]
        authored = model_json(model,
            'Write one short message from the user to their assistant asking only for these missing facts. '
            'Preserve whether each fact is current or historical, without '
            'supplying their answers or tool instructions. '
            'Return ONLY JSON {"question":"one combined message"}.\n'
            f'Original question: {contract.question}\nMissing facts: {json.dumps(hidden)}')
        question = authored['question']
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError('combined follow-up must be one bounded message')
        review = model_json(model,
            'Does this message from the user to their assistant specifically ask for every missing fact, '
            'preserving current versus historical meaning, without supplying answers or '
            'tool instructions? Return ONLY JSON '
            '{"safe":true,"covers_claim_ids":["ids in order"],"reason":"explanation"}.\n'
            f'Original question: {contract.question}\nMissing facts: {json.dumps(hidden)}\n'
            f'Proposed message: {question}')
        origin = 'spark-combined-message'
    if (review.get('safe') is not True or review.get('covers_claim_ids') != claim_ids or
            not isinstance(review.get('reason'), str) or not review['reason'].strip()):
        save(path.with_suffix('.rejected.json'), {'question': question, 'review': review})
        raise ValueError('combined follow-up failed neutral/complete coverage review')
    body = {**binding, 'question': question, 'review': review, 'origin': origin}
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
            plan.get('review', {}).get('covers_claim_ids') != binding['claim_ids']):
        raise ValueError('combined follow-up hash/coverage/contract mismatch')
    return plan


def run_followups(*, contract, plan: dict, grade, initial_turn, output: Path,
                  judge, home: Path, allowlist: str, extra_env: dict | None,
                  source_tools=(), run_turn=None, budget_snapshot=None,
                  question_model=None, question_dir: Path | None = None) -> dict:
    """Ask once for all initially failed claims in the real recall conversation."""
    run_turn = run_turn or harness.run_turn
    budget_snapshot = budget_snapshot or require_live_cost_boundary
    attempts = []
    initial_metrics = turn_metrics(initial_turn, source_tools=source_tools)
    report = {'state': 'running', 'plan_hash': plan['plan_hash'], 'attempts': attempts,
              'protocol_version': 3, 'initial_metrics': initial_metrics,
              'policy': 'one combined follow-up for all initially missed/misrepresented facts'}
    output.mkdir(parents=True, exist_ok=True)
    # A restarted caller cannot ask the same questions again or replace a failed attempt.
    with (output / 'recovery.json').open('x') as record:
        record.write(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    slots = {s.id: s for s in grade.slots}
    selected = [c for c in contract.claims if slots[c.id].verdict != 'correct']
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
            authored = combined_plan(contract, plan, selected, question_model or judge,
                                     question_dir or output / 'question-plans')
            question = authored['question']
            attempt.update(question=question, question_hash=authored['question_hash'])
            save(directory / 'question-plan.json', authored)
            before = budget_snapshot()
            turn = run_turn(question, at=initial_turn.at, home=home, allowlist=allowlist,
                            extra_env=extra_env, timeout_s=180, session_id=initial_turn.session_id)
            save(directory / 'turn.json', vars(turn))
            attempt['metrics'] = turn_metrics(turn, initial_turn, source_tools=source_tools)
            attempt['gateway_window'] = cost_window(before, budget_snapshot())
            if turn.session_id != initial_turn.session_id or turn.returncode or turn.policy_blocks or not turn.answer.strip():
                raise ValueError('follow-up turn failed, changed session, or was policy blocked')
            followup_grade = grade_semantic(grade.checkpoint, turn.prompt, turn.answer,
                tuple(Claim(c.id, c.statement, c.evidence) for c in selected), judge,
                record_path=directory / 'judgment.json')
            save(directory / 'grade.json', {'claims': [vars(s) for s in followup_grade.slots]})
            attempt.update(state='graded', verdicts=[vars(s) for s in followup_grade.slots])
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
                    output: Path) -> list[str]:
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
        failed = [r['id'] for r in rows if r['verdict'] != 'correct']
        grouped = report.get('protocol_version', 1) in {2, 3}
        if grouped:
            check(len(report['attempts']) == int(bool(failed)) and
                  (not failed or report['attempts'][0]['claim_ids'] == failed),
                  'expected exactly one combined follow-up covering all initially failed claims')
            check(len(list(output.glob('*/turn.json'))) == int(bool(failed)), 'unexpected combined follow-up turn count')
        else:
            check([a['claim_id'] for a in report['attempts']] == failed,
                  'expected exactly one follow-up per initially failed claim, in order')
            check(len(list(output.glob('fact-*/turn.json'))) == len(failed), 'unexpected follow-up turn count')
        check(report['initial_metrics'] == turn_metrics(initial_turn), 'initial effort mismatch')
        previous = initial_turn
        for attempt in report['attempts']:
            if grouped:
                directory = output / 'combined'
                selected_ids = attempt['claim_ids']
                frozen_question = load_combined_plan(directory / 'question-plan.json', {
                    'contract_hash': contract.contract_hash, 'component_plan_hash': plan['plan_hash'],
                    'claim_ids': failed})
                check(attempt['question_hash'] == frozen_question['question_hash'], 'combined question hash mismatch')
                question = frozen_question['question']
            else:
                index = ids.index(attempt['claim_id'])
                selected_ids = [attempt['claim_id']]
                directory = output / f'fact-{index + 1}'
                question = plan['questions'][index]['question']
            check(attempt['question'] == question, 'follow-up question changed')
            turn = harness.TurnResult(**read(directory / 'turn.json'))
            expected_prompt = (f'[Simulated now: {initial_turn.at}. Treat this as the current date and '
                               f'time for everything in this turn.]\n\n{question}')
            check(turn.prompt == expected_prompt and turn.at == initial_turn.at, 'follow-up clock/prompt mismatch')
            check(turn.session_id == initial_turn.session_id, 'follow-up lost recall conversation')
            check(not turn.returncode and not turn.policy_blocks, 'follow-up turn failed/blocked')
            check(attempt['metrics'] == turn_metrics(turn, previous), 'follow-up tool/token metrics mismatch')
            judgment = read(directory / 'judgment.json')
            check(judgment['question'] == turn.prompt and judgment['answer'] == turn.answer,
                  'follow-up judgment context mismatch')
            judgments = read(directory / 'grade.json')['claims']
            check(json.loads(judgment['response'])['verdicts'] == judgments,
                  'follow-up grade differs from recorded judge response')
            selected = [c for c in contract.claims if c.id in selected_ids]
            check(judgment['claims'] == [{'id': c.id, 'description': c.statement,
                                         'evidence': list(c.evidence)} for c in selected],
                  'follow-up judge received a different claim')
            check([r['id'] for r in judgments] == selected_ids and
                  all(r['verdict'] in VERDICTS for r in judgments) and
                  (judgments == attempt['verdicts'] if grouped else judgments[0]['verdict'] == attempt['verdict']),
                  'follow-up claim judgment mismatch')
            check(attempt['state'] == 'graded', 'follow-up ungraded')
            previous = turn
        check(report['score'] == recovery_score(grade, turn_metrics(initial_turn), report['attempts']),
              'recovery score mismatch or first-answer penalty erased')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append(f'follow-up audit failed: {type(exc).__name__}: {exc}')
    return errors
