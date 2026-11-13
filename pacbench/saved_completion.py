"""Grade a saved terminal answer without rerunning a provider or changing its run.

This deliberately narrow recovery accepts only the final frozen checkpoint,
with every preceding checkpoint complete. Its separately hashed overlay leaves
all original answers, grades, ingestion receipts and failed attempts intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import harness
from .clock_context import judge_question
from .evaluation import load_evaluations, judge_evidence
from .followups import natural_eligibility, run_followups, save
from .native_checks import diagnostic_inputs, session_key
from .native_pilot import read_prepared
from .semantic_grade import Claim, grade_native_semantic


def _read(path):
    return json.loads(path.read_text())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(component, provider):
    component = Path(component).resolve()
    summary = _read(component / 'summary.json')
    source = (component / summary['source']).resolve()
    messages, _, resolved = read_prepared(source, 150)
    plan, checks = load_evaluations(component, messages, resolved)
    arm = component / 'repeat-1' / provider
    report = _read(arm / 'evaluation.json')
    if [r['id'] for r in report['checks']] != [r['id'] for r, _, _ in checks]:
        raise ValueError('Saved evaluation differs from frozen plan')
    row, contract, policy = checks[-1]
    if (any(r['state'] != 'diagnostic' or r.get('recovery', {}).get('state', 'complete') != 'complete'
            for r in report['checks'][:-1]) or report['checks'][-1]['state'] != 'ungraded'):
        raise ValueError('Only one missing terminal judgment can be recovered')
    directory = arm / 'turns' / row['id']
    if any((directory / name).exists() for name in ('judgment.json', 'grade.json', 'followups/recovery.json')):
        raise ValueError('A successful or partially graded checkpoint already exists')
    turn = harness.TurnResult(**_read(directory / 'turn.json'))
    if (turn.returncode or turn.policy_blocks or not turn.answer.strip() or not turn.session_id
            or turn.at != row['at'] or turn.prompt != harness.question_prompt(contract.question, row['at'], summary['question_prompt_protocol'])):
        raise ValueError('Saved answer execution, policy or clock is invalid')
    # A fresh terminal session avoids reconstructing or silently changing the
    # inherited public conversation and its source-fallback history.
    if any(session_key(r, plan['context']) == session_key(row, plan['context']) for r, _, _ in checks[:-1]):
        raise ValueError('Terminal answer must have an independent frozen session')
    source_state = _read(directory / 'source-state.json')
    receipts = sorted((arm / 'ingestion').glob('*.json'))
    if (source_state['at'] != row['at'] or not receipts
            or [p.stem for p in receipts] != source_state['ingested_batches']
            or any(_read(p).get('ready') is not True for p in receipts)):
        raise ValueError('Complete import receipts are required')
    files = [component / 'summary.json', component / 'evaluation-plan.json',
             component / 'evaluations' / row['id'] / 'contract.json',
             component / 'evaluations' / row['id'] / 'followup-plan.json',
             arm / 'evaluation.json', directory / 'turn.json', directory / 'status.json',
             directory / 'source-state.json', directory / 'usage.json', *receipts]
    for previous, _, _ in checks[:-1]:
        d = arm / 'turns' / previous['id']
        files += [d / 'status.json', d / 'turn.json', d / 'grade.json']
        files += [p for p in (d / 'followups/recovery.json', d / 'behavior.json') if p.exists()]
    bindings = {str(p.relative_to(component)): _sha(p) for p in files}
    evidence = judge_evidence(component, messages, resolved, row, plan['context'],
                              diagnostic_version=summary['evidence_diagnostic_protocol'])
    return summary, row, contract, policy, turn, evidence, bindings


def run(component, provider, overlay, judge):
    component, overlay = Path(component).resolve(), Path(overlay).resolve()
    summary, row, contract, policy, turn, evidence, bindings = prepare(component, provider)
    if overlay.parent != component / 'reviews' or overlay.exists():
        raise ValueError('Use a new direct child of the original run reviews directory')
    overlay.mkdir(parents=True)
    manifest = {'version': 'saved-terminal-completion-v1', 'provider': provider,
                'checkpoint': row['id'], 'source_files': bindings,
                'scope': 'New judge only; original provider answer and completed checks preserved'}
    save(overlay / 'manifest.json', manifest)
    grade = grade_native_semantic(row['id'], judge_question(turn), turn.answer,
        tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
        record_path=overlay / 'judgment.json', assess_recovery=True,
        assess_interaction='session' in row,
        diagnostic_inputs=diagnostic_inputs(turn, contract, evidence,
            diagnostic_version=summary['evidence_diagnostic_protocol']),
        diagnostic_version=summary['evidence_diagnostic_protocol'], judge_protocol=summary['judge_protocol'])
    save(overlay / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
        'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots],
        'recovery': grade.recovery, 'interaction': grade.interaction,
        'evidence_diagnostic': grade.evidence_diagnostic})
    selected, _ = natural_eligibility(grade)
    if selected:
        save(overlay / 'result.json', {'state': 'followup-required', 'provider_calls': 0,
            'eligible_claim_ids': selected, 'reason': 'Requires explicit native-session recovery; saved-only recovery stops here'})
        return _read(overlay / 'result.json')
    def no_provider(*args, **kwargs):
        raise RuntimeError('Saved-answer recovery cannot execute provider calls')
    recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
        output=overlay / 'followups', judge=judge, home=component / 'repeat-1' / provider / 'hermes-home',
        allowlist='', extra_env={}, run_turn=no_provider,
        provenance_version=summary['provenance_version'], evidence_by_id=evidence,
        diagnostic_version=summary['evidence_diagnostic_protocol'], judge_protocol=summary['judge_protocol'])
    if any(_sha(component / p) != sha for p, sha in bindings.items()):
        raise ValueError('Original run changed during recovery')
    result = {'state': 'complete', 'provider': provider, 'checkpoint': row['id'],
              'provider_calls': 0, 'original_files_unchanged': True, 'score': recovery['score']}
    save(overlay / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', type=Path)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--overlay', type=Path, required=True)
    args = parser.parse_args()
    # Validate before installing transport or creating any paid call.
    prepare(args.component, args.provider)
    from .openrouter_direct import direct_environment
    from .gateway_client import client_settings
    from .model_calls import ModelClient
    from .openrouter_route import MODEL
    key = os.environ['OPENROUTER_API_KEY']
    with direct_environment(key):
        base, token = client_settings('pacbench-judge')
        judge = ModelClient(key=token, model=MODEL, url=base + '/chat/completions',
                            record_dir=args.overlay / 'model-calls', reasoning_effort='low',
                            json_mode=True, timeout_s=600)
        print(json.dumps(run(args.component, args.provider, args.overlay, judge)))


if __name__ == '__main__':
    main()
