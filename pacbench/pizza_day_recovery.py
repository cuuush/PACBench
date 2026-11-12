"""Plan, but never approve or execute, a legacy Pizza Day re-review.

The archived bundle remains the authority for its historical IDs and clocks.
This command only reads it and emits a review packet to standard output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .corpus_review import VERSION, review_prompt
from .evaluation import contract_evidence
from .frozen import load_bundle, messages_for
from .preparation_audit import audit_prepared
from .prepare import resolve_spec
from .semantic_contract import ContractClaim, _dialogue, load_contract, shared_context_review_prompt


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--source-only', action='store_true',
                        help='emit only original sources and public question times for an independent audit')
    args = parser.parse_args()
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


if __name__ == '__main__':
    raise SystemExit(main())
