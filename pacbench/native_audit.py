"""Offline integrity checks for native ingestion/recall trials, without scoring text."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract
from .followups import load_questions, audit_followups
from .harness import TurnResult
from .evaluation import load_evaluations
from .native_checks import audit_checks
from .tool_policy import SUPPORTED_VERSIONS, policy_record
from .source_policy import source_read_blocked


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
        for result in summary['results']:
            label = f"repeat-{result.get('repetition', '?')}/{result.get('provider', '?')}"
            try:
                arm = root / f"repeat-{result['repetition']}" / result['provider']
                label = str(arm.relative_to(root))
                if summary.get('ingestion_mode')=='documents' and result['state']=='blocked':
                    from .document_ingestion import BLOCKERS
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
                if not reviewable:
                    # A blocked arm has no live trace; keep the failure and audit later arms.
                    continue
                if summary.get('tool_policy') in SUPPORTED_VERSIONS:
                    from .context_isolation import actual_context, VERSION
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
                    check(bool(allowed) and all('*' not in n for n in allowed),
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
                            context = actual_context(arm / 'hermes-home', data.get('session_id'),
                                Path(workspace_record['workspace']),
                                prompt_hash=(data.get('context_isolation') or {}).get('system_prompt_sha256'))
                            check(context['state'] == 'pass',
                                  f'{label}: {context.get("reason", "recall context isolation failed")}')
                            check(data.get('context_isolation') == context,
                                  f'{label}: saved recall context receipt differs from native prompt')
                            check(not data.get('policy_blocks') and all(
                                c.get('name') in allowed and not source_read_blocked(
                                    c.get('name'),c.get('input',c.get('arguments',{})),rules) for c in data['tool_calls']),
                                f'{label}: non-provider tool attempted in {path.relative_to(arm)}')
                from .document_audit import audit_ingestion
                errors.extend(f'{label}: {e}' for e in audit_ingestion(root,arm,messages,summary))
                if evaluations:
                    errors.extend(f'{label}: {e}' for e in audit_checks(evaluations, evaluation_plan['context'], arm))
                    check(result['checks'] == read(arm / 'evaluation.json')['checks'], f'{label}: evaluation summary mismatch')
                    continue
                recall = read(arm / 'recall.json')
                check(recall['prompt'] == clock_prompt(spec['question_at'], contract.question), f'{label}: recall/clock mismatch')
                check(recall['at'] == spec['question_at'], f'{label}: recall time mismatch')
                check(bool(recall['session_id']), f'{label}: recall session missing')
                check(not recall['returncode'] and not recall['policy_blocks'], f'{label}: recall failure')
                judgment = read(arm / 'judgment.json')
                check(judgment['question'] == recall['prompt'], f'{label}: judge lacks recall clock context')
                check(judgment['answer'] == recall['answer'], f'{label}: judge answer mismatch')
                grade = read(arm / 'grade.json')
                check(sorted(c['id'] for c in grade['claims']) == sorted(c.id for c in contract.claims), f'{label}: incomplete judgments')
                if plan:
                    check(json.loads(judgment['response']).get('recovery') == grade.get('recovery'), f'{label}: recovery eligibility differs from judge')
                    check(json.loads(judgment['response'])['verdicts'] == grade['claims'],
                          f'{label}: initial grade differs from recorded judge response')
                    errors.extend(f'{label}: {e}' for e in audit_followups(contract=contract, plan=plan,
                        initial_turn=TurnResult(**recall), initial_grade=grade, output=arm / 'followups'))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                errors.append(f'{label}: {type(exc).__name__}: {exc}')
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
