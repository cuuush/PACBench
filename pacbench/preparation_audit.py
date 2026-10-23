"""Validate frozen preparation without running a model or a memory provider."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract
from .followups import load_questions
from .evidence_store import read_json


def audit_scenes(root, resolved, life_id, messages):
    """Bind frozen message bodies to the actual saved passing checker input."""
    if not resolved.get('scenes'):return []  # Older minimal portable corpora.
    from dataclasses import fields
    from .authoring import ScenePlan,build_scene_observations
    from .followups import digest
    names={f.name for f in fields(ScenePlan)}
    rows=json.loads((root/'scenes.json').read_text())['scenes']
    if [r['scene_id'] for r in rows]!=[s['id'] for s in resolved['scenes']]:
        return ['Source realization list differs from resolved scene order']
    expected=[];errors=[]
    for row,spec in zip(rows,resolved['scenes']):
        plan=ScenePlan(**{k:v for k,v in spec.items() if k in names})
        built=[o.public() for o in build_scene_observations(life_id,plan,row['lines'])]
        expected.extend(built)
        if row['source_ids']!=[m['source_id'] for m in built] or row['record']['status']!='ok' or row['verdict'].get('supported') is not True:
            errors.append(f"{plan.id}: incomplete source verification")
        if not row.get('verification_binding'):continue  # Legacy receipts keep their original protocol.
        paths=row['record'].get('judgment_paths',[])
        review=read_json(root/'scene-verifications'/Path(paths[-1]).name) if paths else {}
        binding=digest({'version':review.get('spec',{}).get('judge_prompt_version'),
                        'prompt':review.get('judge_prompt')})
        if (review.get('passed') is not True or review.get('spec')!=spec or
                review.get('dialogue')!=[f'{s}: {t}' for s,t in row['lines']] or
                review.get('verdict')!=row['verdict'] or binding!=row['verification_binding']):
            errors.append(f'{plan.id}: source differs from its passing checker input')
        if review.get('model_call'):
            call=read_json(root/'model-calls'/Path(review['model_call']).name)
            if (call.get('status')!='ok' or call.get('text')!=review.get('judge_response') or
                    call.get('request',{}).get('messages')!=[{'role':'user','content':review.get('judge_prompt')}]):
                errors.append(f'{plan.id}: checker receipt differs from actual model call')
        else:errors.append(f'{plan.id}: missing actual checker model receipt')
    if expected!=messages:errors.append('Frozen source bodies or metadata differ from verified scenes')
    return errors


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
        has_checks=(root/'evaluation-plan.json').exists()
        if contract.contract_hash != summary['contract_hash'] or not has_checks and contract.evidence_hash != evidence_hash:
            errors.append('Frozen contract differs from source messages or summary')
        if not has_checks and any(not c.evidence or set(c.evidence) - set(ids) for c in contract.claims):
            errors.append('Claim cites unavailable source messages')
        load_questions(root / 'followup-plan.json', contract)
        resolved = json.loads((root / 'resolved-spec.json').read_text())
        errors.extend(audit_scenes(root,resolved,next(iter(bundle['lives'])),messages))
        if summary.get('corpus_review_version') and not (root/'realism-review.json').exists():
            errors.append('Required corpus realism review is missing')
        if (root/'realism-review.json').exists():
            from .followups import digest
            quality=json.loads((root/'realism-review.json').read_text())
            if summary.get('corpus_review_version') and quality.get('review_version') != summary['corpus_review_version']:
                errors.append('Corpus realism review version differs from preparation')
            intent={'scenes':[{k:s[k] for k in ('id','at','thread','scene','facts')} for s in resolved['scenes']],
                    'checks':resolved['checks']}
            if (quality.get('state')!='pass' or quality.get('source_hash')!=digest(messages) or
                    quality.get('intent_hash')!=digest(intent) or quality.get('review',{}).get('issues') or
                    any(quality.get('review',{}).get(k) is not True for k in ('consistent','expectations_supported','natural_enough'))):
                errors.append('Corpus realism review is stale or unresolved')
            call=read_json(root/'model-calls'/Path(quality.get('model_call','')).name)
            if call.get('status')!='ok' or json.loads(call.get('text',''))!=quality.get('review'):
                errors.append('Corpus realism review differs from its actual model receipt')
            from .corpus_review import SUPPORTED_VERSIONS, review_prompt
            if quality.get('review_version') in SUPPORTED_VERSIONS and call.get('request', {}).get('messages') != [
                    {'role': 'user', 'content': review_prompt(messages, resolved,version=quality['review_version'])}]:
                errors.append('Corpus realism review did not receive the actual frozen source and expectations')
        if resolved.get('ingestion_days') is not None:
            from datetime import date, datetime
            from .document_ingestion import daily_batches
            batches=daily_batches(messages,resolved.get('timezone','America/New_York'))
            expected=resolved['ingestion_days']
            days=[date.fromisoformat(day) for day,_ in batches]
            if (len(days)!=expected or not days or (days[-1]-days[0]).days+1!=expected):
                errors.append('Frozen messages do not cover the requested contiguous ingestion days')
            latest=max(datetime.fromisoformat(m['collected_at']) for m in messages)
            if any(datetime.fromisoformat(c['at'])<=latest for c in resolved.get('checks',[])):
                errors.append('Daily-ingestion trial asks questions before all sources arrive')
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
            if plan['checks'][-1]['contract_hash']!=contract.contract_hash:
                errors.append('Root contract differs from final checkpoint')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError, IndexError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            'scope': 'frozen preparation integrity; no provider quality claim'}


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    result=audit_prepared(parser.parse_args().root)
    print(json.dumps(result,indent=2))
    return int(result['state']!='pass')


if __name__=='__main__':raise SystemExit(main())
