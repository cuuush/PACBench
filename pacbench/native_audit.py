"""Offline integrity checks for native transcript-handoff trials, without scoring text."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract


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
        spec = read(root / 'resolved-spec.json')
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        check(digest == contract.evidence_hash == summary['input_hash'], 'exposure hash mismatch')
        check(contract.contract_hash == summary['contract_hash'], 'contract hash mismatch')
        check(len(messages) == summary['message_count'], 'message count mismatch')
        check(bool(summary['results']), 'no provider results')
        for result in summary['results']:
            arm = root / f"repeat-{result['repetition']}" / result['provider']
            label = str(arm.relative_to(root))
            check(result['state'] == 'diagnostic', f'{label}: incomplete arm')
            check(result.get('input_hash') == digest, f'{label}: arm hash mismatch')
            check(result['capability'].get('wiring_verified'), f'{label}: wiring not verified')
            exposure_sessions = []
            days = sorted({datetime.fromisoformat(m['collected_at']).date().isoformat() for m in messages})
            check(len(list(arm.glob('exposure-*.json'))) == len(days), f'{label}: unexpected exposure count')
            for day in days:
                batch = sorted([m for m in messages if datetime.fromisoformat(m['collected_at']).date().isoformat() == day],
                               key=lambda m: (m['collected_at'], m['source_id']))
                at = max(m['collected_at'] for m in batch)
                public = 'Here are my recent messages. Catch up on them for me.\n\n' + '\n'.join(
                    f"[{m['written_at']}] {m['channel']} / {m['thread']} / {m['sender']}: {m['text']}" for m in batch)
                turn = read(arm / f'exposure-{day}.json')
                check(turn['at'] == at and turn['prompt'] == clock_prompt(at, public), f'{label}: exposure/clock mismatch')
                check(not turn['returncode'] and not turn['policy_blocks'], f'{label}: exposure failure')
                exposure_sessions.append(turn['session_id'])
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
