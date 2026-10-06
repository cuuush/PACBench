"""Offline integrity checks for native transcript-handoff trials, without scoring text."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract
from .followups import load_questions, audit_followups
from .harness import TurnResult
from .evaluation import exposure_batches, load_evaluations
from .native_checks import audit_checks


def audit(root: Path) -> dict:
    errors = []
    def read(path):
        return json.loads(path.read_text())
    def check(ok, message):
        if not ok:
            errors.append(message)
    def clock_prompt(at, text):
        return f"[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{text}"
    try:
        summary = read(root / 'summary.json')
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
        check(digest == contract.evidence_hash == summary['input_hash'], 'exposure hash mismatch')
        check(contract.contract_hash == summary['contract_hash'], 'contract hash mismatch')
        check(len(messages) == summary['message_count'], 'message count mismatch')
        check(bool(summary['results']), 'no provider results')
        for result in summary['results']:
            arm = root / f"repeat-{result['repetition']}" / result['provider']
            label = str(arm.relative_to(root))
            if 'process_timezone' in summary:
                check(read(arm / 'simulation-clock.json')['process_timezone'] == summary['process_timezone'],
                      f'{label}: inconsistent provider process timezone')
            check(result['state'] == 'diagnostic', f'{label}: incomplete arm')
            check(result.get('input_hash') == digest, f'{label}: arm hash mismatch')
            check(result['capability'].get('wiring_verified'), f'{label}: wiring not verified')
            if result['state'] != 'diagnostic':
                # A blocked arm has no live trace; keep the failure and audit later arms.
                continue
            exposure_sessions = []
            batches = exposure_batches(messages, spec)
            schedule = summary.get('exposure_schedule')
            if schedule is None:
                schedule = [{'id': day, 'file': f'exposure-{day}.json', 'source_ids': [m['source_id'] for m in batch],
                             'at': max(m['collected_at'] for m in batch)} for day, batch in batches]
            expected_schedule = [{'id': day,
                'file': (f'exposures/{i + 1:03d}.json' if spec.get('exposure_delivery') == 'scenes' else f'exposure-{day}.json'),
                'source_ids': [m['source_id'] for m in batch], 'at': max(m['collected_at'] for m in batch)}
                for i, (day, batch) in enumerate(batches)]
            check(schedule == expected_schedule, f'{label}: exposure schedule differs from frozen input')
            recorded = list(arm.glob('exposure-*.json')) + list((arm / 'exposures').glob('*.json'))
            usage_files = [p for p in recorded if p.name.endswith('.json.usage.json')]
            check(all(str(p.relative_to(arm)) in {r['file'] + '.usage.json' for r in schedule}
                      for p in usage_files), f'{label}: unexpected exposure usage files')
            recorded = [p for p in recorded if p not in usage_files]
            check(sorted(str(p.relative_to(arm)) for p in recorded) == sorted(r['file'] for r in schedule),
                  f'{label}: unexpected exposure files')
            for row, (day, batch) in zip(schedule, batches):
                at = max(m['collected_at'] for m in batch)
                public = 'Here are my recent messages. Catch up on them for me.\n\n' + '\n'.join(
                    f"[{m['written_at']}] {m['channel']} / {m['thread']} / {m['sender']}: {m['text']}" for m in batch)
                turn = read(arm / row['file'])
                check(turn['at'] == at and turn['prompt'] == clock_prompt(at, public), f'{label}: exposure/clock mismatch')
                check(not turn['returncode'] and not turn['policy_blocks'], f'{label}: exposure failure')
                if spec.get('exposure_context', 'isolated') == 'shared' and exposure_sessions:
                    check(turn['session_id'] == exposure_sessions[0], f'{label}: exposure lost shared conversation')
                else:
                    check(bool(turn['session_id']) and turn['session_id'] not in exposure_sessions,
                          f'{label}: exposure session missing or unexpectedly reused')
                exposure_sessions.append(turn['session_id'])
            if evaluations:
                errors.extend(f'{label}: {e}' for e in audit_checks(evaluations, evaluation_plan['context'], arm, exposure_sessions))
                check(result['checks'] == read(arm / 'evaluation.json')['checks'], f'{label}: evaluation summary mismatch')
                continue
            recall = read(arm / 'recall.json')
            check(recall['prompt'] == clock_prompt(spec['question_at'], contract.question), f'{label}: recall/clock mismatch')
            check(recall['at'] == spec['question_at'], f'{label}: recall time mismatch')
            check(bool(recall['session_id']) and recall['session_id'] not in exposure_sessions, f'{label}: recall reused exposure session')
            check(not recall['returncode'] and not recall['policy_blocks'], f'{label}: recall failure')
            judgment = read(arm / 'judgment.json')
            check(judgment['question'] == recall['prompt'], f'{label}: judge lacks recall clock context')
            check(judgment['answer'] == recall['answer'], f'{label}: judge answer mismatch')
            grade = read(arm / 'grade.json')
            check(sorted(c['id'] for c in grade['claims']) == sorted(c.id for c in contract.claims), f'{label}: incomplete judgments')
            if plan:
                if read(arm / 'followups/recovery.json').get('protocol_version') == 5:
                    check(json.loads(judgment['response']).get('recovery') == grade.get('recovery'), f'{label}: recovery eligibility differs from judge')
                check(json.loads(judgment['response'])['verdicts'] == grade['claims'],
                      f'{label}: initial grade differs from recorded judge response')
                errors.extend(f'{label}: {e}' for e in audit_followups(contract=contract, plan=plan,
                    initial_turn=TurnResult(**recall), initial_grade=grade, output=arm / 'followups'))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            'scope': 'native trace integrity, not calibrated provider quality'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    report = audit(args.run)
    print(json.dumps(report, indent=2))
    return int(bool(report['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
