"""Review archived Pizza Day source and calibrate its published judge."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from ..canary_capability import binding, read
from pacbench.corpus.review import VERSION, review_prompt
from pacbench.corpus.evaluation import contract_evidence
from pacbench.corpus.sources import load_bundle, messages_for
from ..live_dispatch import CONDITION, process_identity
from pacbench.corpus.audit import audit_prepared
from pacbench.corpus.prepare import resolve_spec
from pacbench.corpus.contract import ContractClaim, _dialogue, load_contract, shared_context_review_prompt
from ..semantic_grade import judge_prompt
from ..workflows.runner import grade_turn, judge_options
from .pizza_day import BASIS, SPEC, inputs

SCOPE = 'pizza-day-wrong-answer-judge-v1'
NAME = 'judge-control'
PROJECT = 'pacbench-pizza-day-control-judge'
ANSWER = "Your pizza dinner is with Maya tomorrow, Saturday September 12, at Tony's on 8th at 9pm."


def control_input(preparation):
    _, _, turn = inputs(preparation)
    surface = dict(answer=ANSWER, history=[dict(role='user', content=turn.prompt)], conversation_times=[])
    return turn, surface


def validate(scope, bound):
    root = Path(scope['root'])
    if (root != root.resolve() or root.name != NAME or scope['provider'] != NAME
            or scope['run_id'] != root.parent.name or scope['frozen_inputs'] != BASIS
            or scope['spec_sha256'] != SPEC or Path(bound['path']) != root/'workflow-scope.json'
            or scope['projects'] != {NAME: {'judge': PROJECT}} or scope['controller'] != process_identity()):
        raise ValueError('wrong-answer control requires its own judge-only scope')


def admit(scope, project, body):
    from ..openrouter_route import with_route
    from ..workflows.capability import ENV, PROFILE
    turn, surface = control_input(Path(scope['preparation']))
    prompt = judge_prompt(turn.prompt, ANSWER, turn.claims, **judge_options(turn, surface))
    expected = with_route(dict(model='openai/gpt-6-luna', messages=[dict(role='user', content=prompt)],
        stream=True, stream_options={'include_usage':True}, max_completion_tokens=32768,
        response_format={'type':'json_object'}), profile=PROFILE)
    if project != PROJECT or body != expected:
        raise ValueError('unexpected calibration request')
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
        slot=dict(kind='judge', provider=NAME, question_id=turn.id), projects=[project])


def control_main(argv=None):
    from pacbench.workflows import capability as cap
    from ..gateway_client import client_settings
    from ..model_calls import ModelClient
    from ..observational_receipts import credit_snapshot
    from ..openrouter_direct import direct_environment
    from ..openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from ..openrouter_run import fetch_catalog
    from ..workflows.recording import receipt_summary
    from pacbench.providers import run_environment

    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('campaign', 'preparation', 'confirmation'):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args(argv)
    root = args.campaign.resolve()/NAME
    turn, surface = control_input(args.preparation)
    proof = binding(args.confirmation.resolve())
    cap.confirm_setting(read(proof))
    if PROFILE_NAME != cap.PROFILE:
        raise ValueError('Luna profile required before startup')
    fetch_catalog(profile=cap.PROFILE)
    root.mkdir(mode=0o700, exist_ok=False)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'), PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
        PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1', PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
        PACBENCH_DISPATCH_TRACKING=CONDITION, PACBENCH_STOP_FILE=str(root/'STOP'),
        PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent/'ACCOUNT_STOP'))
    with run_environment(settings):
        scope = dict(root=str(root), admission=SCOPE, provider=NAME, run_id=root.parent.name, frozen_inputs=BASIS,
            spec_sha256=SPEC, preparation=str(args.preparation.resolve()), projects={NAME:{'judge':PROJECT}},
            controller=process_identity(), prepaid=credit_snapshot(), auto_recharge_evidence=proof)
        path = root/'workflow-scope.json'
        with path.open('x') as f:
            json.dump(scope, f)
        with run_environment({cap.ENV:json.dumps(binding(path))}):
            ledger = cap.budget()
            report = dict(state='ungraded', condition=SCOPE)
            started = time.monotonic()
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    _, key = client_settings(PROJECT)
                    model = ModelClient(key=key, model=MODEL, record_dir=root/'model-calls', url=CHAT_COMPLETIONS_URL,
                                        json_mode=True, max_attempts=1)
                    grade = grade_turn(turn, surface, model, root/'judgments', require_receipt=True)
                    report.update(state='graded', answer=ANSWER, grade=asdict(grade))
            finally:
                report.update(elapsed_s=time.monotonic()-started, billing=receipt_summary(root, ledger.snapshot(), root.parent.name))
                (root/'result.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))


def review_packet(root: Path) -> dict:
    root = Path(root)
    read = lambda name: json.loads((root / name).read_text())
    summary = read('summary.json')
    bundle = load_bundle(root / 'bundle.json', summary['bundle_hash'])
    if len(bundle['lives']) != 1:
        raise ValueError('Expected exactly one historical source life')
    life_id = next(iter(bundle['lives']))
    messages = messages_for(bundle, life_id)
    ids = [m['source_id'] for m in messages]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Historical source IDs must be present and unique')
    contract = load_contract(root / 'contract.json')
    if contract.contract_hash != summary['contract_hash']:
        raise ValueError('Historical contract differs from summary')
    if any(not c.evidence or set(c.evidence) - set(ids) for c in contract.claims):
        raise ValueError('Historical claims cite missing source IDs')
    original = read('spec.json')
    resolved_old = read('resolved-spec.json')
    target = next(s for s in resolved_old['scenes'] if s['id'] == resolved_old['test_scene_id'])
    # This is a proposed current check, not a reviewed or frozen contract.
    authored = dict(original)
    for key in ('providers', 'prepare_only', 'exposure_context', 'exposure_delivery', 'maintenance_at'):
        authored.pop(key, None)
    authored['checks'] = [{'id': 'pizza-plan', 'at': resolved_old['question_at'],
                           'prompt': contract.question, 'facts': target['facts']}]
    _, _, resolved = resolve_spec(authored, root)
    check = resolved['checks'][0]
    available = contract_evidence(messages, resolved['checks'], check, resolved['evaluation_context'])
    claims = tuple(ContractClaim(c.id, c.statement, tuple(c.evidence)) for c in contract.claims)
    contract_prompt = shared_context_review_prompt(
        claims=claims, author_facts=tuple(check['facts']), dialogue=_dialogue(available),
        question_at=check['at'], user_question=check['prompt'], test_intent=check['prompt'])
    return {
        'state': 'blocked',
        'purpose': 'offline review planning only; not a preparation or approval',
        'source_bundle_hash': summary['bundle_hash'],
        'historical_contract_hash': summary['contract_hash'],
        'historical_audit': audit_prepared(root),
        'source_messages': messages,
        'proposed_spec': authored,
        'compiled_spec': resolved,
        'proposed_claims': [vars(c) for c in claims],
        'fresh_review_inputs': {
            'combined_claim_and_question': {'prompt_format': 'shared-context-v2', 'prompt': contract_prompt},
            'corpus_realism': {'review_version': VERSION, 'prompt': review_prompt(messages, resolved, version=VERSION)},
        },
        'blockers': [
            'Historical scene records lack current source IDs; the current scene compiler generates different opaque message and thread IDs. Resolve source provenance and current audit compatibility before freezing.',
            'The proposed claims and question require a fresh independent combined review with a bound model receipt; historical reviews are not transferred.',
            'The frozen corpus and public questions require a fresh independent corpus realism review with a bound model receipt.',
            'Complete any separately required source-only semantic audit, then pass the actual preparation audit and export a week manifest before provider execution.',
        ],
    }


def review_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--source-only', action='store_true',
                        help='emit only original sources and public question times for an independent audit')
    args = parser.parse_args(argv)
    packet = review_packet(args.root)
    if args.source_only:
        packet = {'source_bundle_hash': packet['source_bundle_hash'],
                  'source_messages': packet['source_messages'],
                  'public_questions': [{k: c[k] for k in ('at', 'prompt')}
                                       for c in packet['compiled_spec']['checks']]}
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    return 0


def dialogue_import(root: Path) -> tuple[dict, dict]:
    """Keep original text, compile new metadata, and require every fresh review."""
    root = Path(root)
    packet = review_packet(root)
    rows = json.loads((root/'scenes.json').read_text())['scenes']
    flattened = [(s, t) for row in rows for s, t in row['lines']]
    if flattened != [(m['sender'], m['text']) for m in packet['source_messages']]:
        raise ValueError('Archived scene dialogue differs from the hash-verified source bundle')
    spec = packet['proposed_spec']
    if len(rows) != len(spec['scenes']) or len({r['scene_id'] for r in rows}) != len(rows):
        raise ValueError('Historical scene inventory differs')
    for scene in spec['scenes']:
        row = next(r for r in rows if r['scene_id'] == scene['id'])
        scene['message_range'] = [len(row['lines']), len(row['lines'])]
        scene['participants'] = list(dict.fromkeys(s for s, _ in row['lines']))
    return spec, {'source_bundle_hash': packet['source_bundle_hash'],
                  'condition': 'historical dialogue text; current compiler identities/timing; fresh reviews',
                  'scenes': {r['scene_id']: r['lines'] for r in rows}}
