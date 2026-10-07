"""Validate frozen preparation without running a model or a memory provider."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract
from .followups import load_questions


def audit_prepared(root: Path) -> dict:
    errors = []
    try:
        summary = json.loads((root / 'summary.json').read_text())
        if summary['state'] != 'prepared' or summary['results']:
            errors.append('Preparation must not execute provider arms')
        bundle = load_bundle(root / 'bundle.json', summary['bundle_hash'])
        if len(bundle['lives']) != 1:
            errors.append('Expected one source life')
        messages = messages_for(bundle, next(iter(bundle['lives'])))
        ids = [m['source_id'] for m in messages]
        if not ids or len(ids) != len(set(ids)):
            errors.append('Missing or duplicate source IDs')
        evidence_hash = hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        contract = load_contract(root / 'contract.json')
        if contract.contract_hash != summary['contract_hash'] or contract.evidence_hash != evidence_hash:
            errors.append('Frozen contract differs from source messages or summary')
        if any(not c.evidence or set(c.evidence) - set(ids) for c in contract.claims):
            errors.append('Claim cites unavailable source messages')
        load_questions(root / 'followup-plan.json', contract)
        resolved = json.loads((root / 'resolved-spec.json').read_text())
        # Re-validate clocks and bounded questions, independent of provider runs.
        from .evaluation import resolve_checks
        normalized = resolve_checks(resolved, resolved['question_at'], resolved.get('timezone', ''))
        if normalized['checks'] != resolved.get('checks', []):
            errors.append('Evaluation checks differ from normalized source spec')
        if (root / 'evaluation-plan.json').exists():
            from .evaluation import load_evaluations
            resolved = json.loads((root / 'resolved-spec.json').read_text())
            plan, _ = load_evaluations(root, messages, resolved)
            if plan['plan_hash'] != summary.get('evaluation_plan_hash'):
                errors.append('Evaluation plan differs from summary')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            'scope': 'frozen preparation integrity; no provider quality claim'}


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    result=audit_prepared(parser.parse_args().root)
    print(json.dumps(result,indent=2))
    return int(result['state']!='pass')


if __name__=='__main__':raise SystemExit(main())
