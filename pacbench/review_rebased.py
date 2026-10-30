"""Reverify explicitly edited source lines against a new dated experiment spec.

This creates a new preparation; it never rewrites source dialogue with a model.
The edits file is a list of {scene_id, line, before, after}, where line is a
zero-based index and before must match the complete original message body.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .authoring import SceneRecord, build_scene_observations, judge_scene
from .corpus_review import VERSION, review_corpus
from .creds import load_driver
from .evaluation import freeze_evaluations
from .followups import freeze_policy, save
from .frozen import bundle_hash, write_bundle
from .gateway_client import client_settings, require_live_cost_boundary
from .model_calls import ModelClient
from .preparation_audit import audit_prepared
from .prepare import export_week, realization_binding, resolve_spec
from .semantic_contract import load_contract


def prepare_rebased(source: Path, spec: dict, edits: list, output: Path, *, resume=False) -> dict:
    source, output = source.resolve(), output.resolve()
    audit = audit_prepared(source)
    if audit['state'] != 'pass':
        raise ValueError('Original preparation failed audit: ' + str(audit['errors']))
    old = json.loads((source / 'scenes.json').read_text())['scenes']
    plans, target, resolved = resolve_spec(spec, output)
    if [row['scene_id'] for row in old] != [plan.id for plan in plans]:
        raise ValueError('Rebased scene IDs and order must match the original')
    indexed = {row['scene_id']: [list(line) for line in row['lines']] for row in old}
    seen = set()
    for edit in edits:
        if set(edit) != {'scene_id', 'line', 'before', 'after'}:
            raise ValueError('Each edit needs scene_id, line, before and after')
        key = (edit['scene_id'], edit['line'])
        if key in seen or type(edit['line']) is not int or edit['line'] < 0:
            raise ValueError('Duplicate or invalid line edit')
        seen.add(key)
        lines = indexed[edit['scene_id']]
        if lines[edit['line']][1] != edit['before'] or not isinstance(edit['after'], str):
            raise ValueError('Edit does not match its original full message')
        lines[edit['line']][1] = edit['after']
    provenance = {'source': str(source),
        'source_bundle_hash': json.loads((source / 'summary.json').read_text())['bundle_hash'],
        'edits': edits}
    if resume:
        if (json.loads((output / 'spec.json').read_text()) != spec or
                json.loads((output / 'resolved-spec.json').read_text()) != resolved or
                json.loads((output / 'rebase-provenance.json').read_text()) != provenance):
            raise ValueError('Resume requires identical source, spec and edits')
        review = output / 'realism-review.json'
        if review.exists() and json.loads(review.read_text()).get('state') != 'pass':
            raise ValueError('Rejected realism review is terminal; preserve it for review')
    else:
        require_live_cost_boundary()
        output.mkdir(parents=True, exist_ok=False)
        save(output / 'spec.json', spec)
        save(output / 'resolved-spec.json', resolved)
        save(output / 'rebase-provenance.json', provenance)
    if resume:
        require_live_cost_boundary()
    save(output / 'experiment-status.json', {'state': 'running', 'stage': 'scene verification'})
    try:
        driver = load_driver()
        key = __import__('os').environ['META_API_KEY']
        url = client_settings('pacbench-judge')[0] + '/chat/completions'
        judge = ModelClient(key=key, model=driver.model_id, record_dir=output / 'model-calls', url=url)
        draft = ModelClient(key=key, model=driver.model_id, record_dir=output / 'model-calls', url=url,
                            reasoning_effort='low')
        messages, rows = [], []
        for plan in plans:
            lines = indexed[plan.id]
            prior = tuple((m['written_at'], m['channel'] + ':' + m['thread'], m['sender'], m['text'])
                          for m in messages if datetime.fromisoformat(m['written_at']) < datetime.fromisoformat(plan.at))
            verdict, pc, rc, path = judge_scene(plan, lines, judge, model=driver.model_id,
                record_dir=output / 'scene-verifications', attempt=1, prior_lines=prior)
            record = SceneRecord(plan.id)
            record.status = 'ok'
            record.judge_calls = 1
            record.prompt_chars, record.response_chars = pc, rc
            if path:
                record.judgment_paths.append(path)
            new = [o.public() for o in build_scene_observations(resolved['id'], plan, lines)]
            messages.extend(new)
            rows.append({'scene_id': plan.id, 'lines': lines, 'source_ids': [m['source_id'] for m in new],
                         'record': vars(record), 'verdict': verdict,
                         'verification_binding': realization_binding(plan, lines, prior)})
            save(output / 'scenes.json', {'scenes': rows})
        bundle = {'bundle_version': '0.3.0', 'lives': {resolved['id']: {
            'lineage': resolved['id'], 'messages': messages}}}
        write_bundle(bundle, output / 'bundle.json')
        save(output / 'bundle-provenance.json', {'hash': bundle_hash(bundle), 'source': 'verified-scenes'})
        save(output / 'experiment-status.json', {'state': 'running', 'stage': 'contract review'})
        plan = freeze_evaluations(resolved, messages, target, judge, output, resume=resume, generate=draft)
        if plan.get('errors'):
            raise ValueError('Evaluation review failed: ' + str(plan['errors']))
        final = output / 'evaluations' / plan['checks'][-1]['id'] / 'contract.json'
        (output / 'contract.json').write_bytes(final.read_bytes())
        contract = load_contract(output / 'contract.json')
        review_corpus(messages, resolved, judge, output)
        freeze_policy(contract, output / 'followup-plan.json')
        summary = {'state': 'prepared', 'results': [], 'message_count': len(messages),
                   'noise': resolved['noise'], 'corpus_review_version': VERSION,
                   'contract_hash': contract.contract_hash, 'bundle_hash': bundle_hash(bundle),
                   'evaluation_plan_hash': plan['plan_hash']}
        save(output / 'summary.json', summary)
        result = audit_prepared(output)
        save(output / 'run-audit.json', result)
        if result['state'] != 'pass':
            raise ValueError('Rebased preparation failed audit: ' + str(result['errors']))
        export_week(output, output / 'week.json')
        save(output / 'experiment-status.json', {'state': 'prepared', 'stage': 'finished'})
        return summary
    except Exception as exc:
        save(output / 'experiment-status.json', {'state': 'incomplete', 'error': type(exc).__name__})
        save(output / 'summary.json', {'state': 'incomplete', 'results': [], 'error': type(exc).__name__})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--edits', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='Reuse only exact successful model receipts')
    args = parser.parse_args()
    prepare_rebased(args.source, json.loads(args.spec.read_text()), json.loads(args.edits.read_text()), args.output,
                    resume=args.resume)


if __name__ == '__main__':
    main()
