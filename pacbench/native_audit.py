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


def audit(root: Path, *, providers=None) -> dict:
    errors = []
    selected=set(providers) if providers is not None else None
    def read(path):
        return json.loads(path.read_text())
    def check(ok, message):
        if not ok:
            errors.append(message)
    def clock_prompt(at, text):
        return f"[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{text}"
    try:
        summary = read(root / 'summary.json')
        from .semantic_grade import DIAGNOSTIC_VERSIONS, LEGACY_DIAGNOSTIC_VERSION, JUDGE_PROTOCOLS, LEGACY_JUDGE_PROTOCOL
        if summary.get('provenance_version', 'source-provenance-v1') not in {'source-provenance-v1', 'source-provenance-v2'}:
            raise ValueError('Unsupported provenance protocol')
        if 'evidence_diagnostic_protocol' in summary and summary['evidence_diagnostic_protocol'] not in DIAGNOSTIC_VERSIONS:
            raise ValueError('Unsupported evidence diagnostic protocol')
        if summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) not in JUDGE_PROTOCOLS:
            raise ValueError('Unsupported semantic judge protocol')
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
                if not reviewable and not ((arm/'recall.json').exists() or list((arm/'turns').glob('*/turn.json'))):
                    # Unfinished arms still need their existing live traces
                    # checked; only a genuinely unexecuted arm has none.
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
                try:errors.extend(f'{label}: {e}' for e in audit_ingestion(root,arm,messages,summary))
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    errors.append(f'{label}: ingestion audit {type(exc).__name__}')
                if evaluations:
                    from .evaluation import judge_evidence
                    def evidence_for_check(row):
                        return judge_evidence(root, messages, spec, row, evaluation_plan['context'], diagnostic_version=summary.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION))
                    errors.extend(f'{label}: {e}' for e in audit_checks(
                        evaluations, evaluation_plan['context'], arm,
                        provenance_version=summary.get('provenance_version', 'source-provenance-v1'),
                        evidence_by_id=evidence_for_check if summary.get('evidence_diagnostic_protocol') else None,
                        diagnostic_version=summary.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION),
                        judge_protocol=summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)))
                    from .native_writes import audit_ready
                    errors.extend(f'{label}: {e}' for e in audit_ready(evaluations,evaluation_plan['context'],arm,
                        result.get('write_readiness_protocol',summary.get('write_readiness_protocol'))))
                    check(result['checks'] == read(arm / 'evaluation.json')['checks'], f'{label}: evaluation summary mismatch')
                    continue
                recall = read(arm / 'recall.json')
                check(recall['prompt'] == clock_prompt(spec['question_at'], contract.question), f'{label}: recall/clock mismatch')
                check(recall['at'] == spec['question_at'], f'{label}: recall time mismatch')
                check(bool(recall['session_id']), f'{label}: recall session missing')
                check(not recall['returncode'] and not recall['policy_blocks'], f'{label}: recall failure')
                judgment = read(arm / 'judgment.json')
                check(judgment.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) == summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL), f'{label}: judge protocol mismatch')
                check(judgment['question'] == recall['prompt'], f'{label}: judge lacks recall clock context')
                check(judgment['answer'] == recall['answer'], f'{label}: judge answer mismatch')
                grade = read(arm / 'grade.json')
                check(sorted(c['id'] for c in grade['claims']) == sorted(c.id for c in contract.claims), f'{label}: incomplete judgments')
                if summary.get('evidence_diagnostic_protocol'):
                    from .native_checks import diagnostic_inputs
                    from .semantic_grade import Claim, validate_diagnostic, judge_prompt
                    from .evaluation import source_evidence
                    observed_turn = TurnResult(**recall)
                    inputs = diagnostic_inputs(observed_turn, contract, source_evidence(messages, summary['evidence_diagnostic_protocol']), diagnostic_version=summary['evidence_diagnostic_protocol'])
                    claims = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
                    response = json.loads(judgment['response'])
                    if summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) != LEGACY_JUDGE_PROTOCOL:
                        from .semantic_grade import audit_native_judge_attempts
                        audit_native_judge_attempts(arm/'judgment.json', question=recall['prompt'], answer=recall['answer'], claims=claims,
                            judge_protocol=summary['judge_protocol'], assess_recovery=True, diagnostic_inputs=inputs,
                            diagnostic_version=summary['evidence_diagnostic_protocol'])
                    check(judgment.get('diagnostic_protocol') == summary['evidence_diagnostic_protocol'] and judgment.get('diagnostic_inputs') == inputs,
                        f'{label}: evidence diagnostic scope mismatch')
                    check(judgment.get('prompt') == judge_prompt(recall['prompt'], recall['answer'], claims,
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
                        judge_protocol=summary.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                errors.append(f'{label}: {type(exc).__name__}: {exc}')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            **({'selected_providers':sorted(selected)} if providers is not None else {}),
            'scope': 'native trace integrity, not calibrated provider quality'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--providers',nargs='+',help='Audit only these explicitly selected provider arms')
    args = parser.parse_args()
    report = audit(args.run,providers=args.providers)
    print(json.dumps(report, indent=2))
    return int(bool(report['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
