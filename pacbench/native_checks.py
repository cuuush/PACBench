"""Sequential native Hermes questions with independently frozen fact contracts."""
from __future__ import annotations

import json
from pathlib import Path

from . import harness
from .usage import tracked_turn
from .followups import audit_followups, run_followups, save, tool_profile
from .semantic_grade import Claim, grade_semantic


def extend_history(history, turn):
    return history + [{'role': 'user', 'content': turn.prompt},
                      {'role': 'assistant', 'content': turn.answer}]


def extend_path(inherited, turn):
    profile = tool_profile(turn)
    return {'source_fallback': inherited['source_fallback'] or profile['source_fallback_attempted'],
            'unclassified': inherited['unclassified'] or bool(profile['unclassified_operations'])}


def run_checks(checks, context, root, judge, allowlist, env, *, question_dir,
               run_turn=None, budget_snapshot=None):
    run_turn = run_turn or harness.run_turn
    history, previous, session = [], None, None
    inherited = {'source_fallback': False, 'unclassified': False}
    report = {'state': 'running', 'context': context, 'checks': []}
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'evaluation.json').open('x') as record:
        record.write(json.dumps(report, indent=2) + '\n')
    for row, contract, policy in checks:
        shared = context == 'shared'
        directory = root / 'turns' / row['id']
        item = {'id': row['id'], 'at': row['at'], 'contract_hash': contract.contract_hash,
                'state': 'invalid', 'reason': 'turn not complete'}
        report['checks'].append(item)
        save(root / 'evaluation.json', report)
        turn = tracked_turn(run_turn, contract.question, usage_path=directory / "usage.json", at=row['at'], home=root / 'hermes-home',
            allowlist=allowlist, extra_env=env, timeout_s=180,
            session_id=session if shared else None)
        save(directory / 'turn.json', vars(turn))
        if (turn.returncode or turn.policy_blocks or not turn.answer.strip() or not turn.session_id or
                shared and session and turn.session_id != session):
            item['reason'] = 'evaluation turn failed, blocked, or lost conversation continuity'
            break
        prior = history if shared else []
        grade = grade_semantic(row['id'], turn.prompt, turn.answer,
            tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
            record_path=directory / 'judgment.json', conversation=prior, assess_recovery=True)
        save(directory / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
            'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots], 'recovery': grade.recovery})
        recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
            output=directory / 'followups', judge=judge, home=root / 'hermes-home',
            allowlist=allowlist, extra_env=env, question_dir=question_dir,
            previous_turn=previous if shared else None, conversation=prior,
            inherited_path=inherited if shared else None, run_turn=run_turn,
            budget_snapshot=budget_snapshot)
        item.update(state='diagnostic' if recovery['state'] == 'complete' else 'invalid',
                    reason='' if recovery['state'] == 'complete' else 'follow-up diagnostics incomplete',
                    recovery={'state': recovery['state'], 'score': recovery['score'],
                              'attempts': recovery['attempts']})
        history = extend_history(prior, turn)
        inherited = extend_path(inherited if shared else {'source_fallback': False, 'unclassified': False}, turn)
        previous, session = turn, turn.session_id
        followup_file = directory / 'followups' / 'combined' / 'turn.json'
        if followup_file.exists():
            previous = harness.TurnResult(**json.loads(followup_file.read_text()))
            history = extend_history(history, previous)
            inherited = extend_path(inherited, previous)
        save(directory / 'status.json', item)
        save(root / 'evaluation.json', report)
        if item['state'] != 'diagnostic':
            break
    report['state'] = ('diagnostic' if len(report['checks']) == len(checks) and
                       all(x['state'] == 'diagnostic' for x in report['checks']) else 'invalid')
    save(root / 'evaluation.json', report)
    return report


def audit_checks(checks, context, root):
    errors = []
    def check(ok, text):
        if not ok:
            errors.append(text)
    def read(path):
        return json.loads(path.read_text())
    history, previous, session = [], None, None
    inherited = {'source_fallback': False, 'unclassified': False}
    try:
        report = read(root / 'evaluation.json')
        check(report['state'] == 'diagnostic' and report['context'] == context, 'incomplete evaluation sequence')
        check([r['id'] for r in report['checks']] == [r['id'] for r, _, _ in checks], 'evaluation checks differ from frozen plan')
        check(sorted(p.name for p in (root / 'turns').iterdir()) == sorted(r['id'] for r, _, _ in checks), 'unexpected evaluation folders')
        seen = set()
        for (row, contract, policy), item in zip(checks, report['checks']):
            directory = root / 'turns' / row['id']
            turn = harness.TurnResult(**read(directory / 'turn.json'))
            expected = f"[Simulated now: {row['at']}. Treat this as the current date and time for everything in this turn.]\n\n{contract.question}"
            check(turn.at == row['at'] and turn.prompt == expected, f"{row['id']}: evaluation question/clock mismatch")
            check(not turn.returncode and not turn.policy_blocks and bool(turn.answer.strip()), f"{row['id']}: failed evaluation turn")
            if context == 'shared' and session:
                check(turn.session_id == session, f"{row['id']}: shared conversation changed")
            else:
                check(bool(turn.session_id) and turn.session_id not in seen, f"{row['id']}: evaluation reused an isolated session")
            seen.add(turn.session_id)
            prior = history if context == 'shared' else []
            judgment, grade = read(directory / 'judgment.json'), read(directory / 'grade.json')
            check(judgment['question'] == turn.prompt and judgment['answer'] == turn.answer and
                  judgment.get('conversation') == prior, f"{row['id']}: judge conversation mismatch")
            check(json.loads(judgment['response']).get('recovery') == grade.get('recovery'), f"{row['id']}: recovery eligibility differs from judge")
            check(json.loads(judgment['response'])['verdicts'] == grade['claims'], f"{row['id']}: grade differs from judge")
            check(judgment['claims'] == [{'id': c.id, 'description': c.statement, 'evidence': list(c.evidence)} for c in contract.claims],
                  f"{row['id']}: judge received different criteria")
            errors.extend(f"{row['id']}: {e}" for e in audit_followups(contract=contract, plan=policy,
                initial_turn=turn, initial_grade=grade, output=directory / 'followups',
                previous_turn=previous if context == 'shared' else None, conversation=prior,
                inherited_path=inherited if context == 'shared' else None))
            recovery = read(directory / 'followups' / 'recovery.json')
            check(item['state'] == 'diagnostic' and item['contract_hash'] == contract.contract_hash and
                  item['at'] == row['at'] and item['recovery']['score'] == recovery['score'] and
                  item['recovery']['attempts'] == recovery['attempts'], f"{row['id']}: summary/score mismatch")
            history = extend_history(prior, turn)
            inherited = extend_path(inherited if context == 'shared' else {'source_fallback': False, 'unclassified': False}, turn)
            previous, session = turn, turn.session_id
            followup_file = directory / 'followups' / 'combined' / 'turn.json'
            if followup_file.exists():
                previous = harness.TurnResult(**read(followup_file))
                history = extend_history(history, previous)
                inherited = extend_path(inherited, previous)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f'evaluation audit: {exc}')
    return errors
