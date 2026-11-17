"""Independent checkpoint-scoped source audits; never repair model verdicts in place."""
from pathlib import Path
import json
from .followups import digest, save
from .corpus_epistemic import eligible_evidence, prompt as whole_prompt, response_schema, validation_errors
from .corpus_review import review_intent

VERSION = 'source-realism-v12-checkpoint-isolated'
BOUND_VERSION = 'source-realism-v13-checkpoint-locator-bound'

def prompt(messages, resolved, check):
    scope = eligible_evidence(messages, resolved, check['id'])
    from .authoring import ScenePlan, build_scene_observations
    from dataclasses import fields
    names={f.name for f in fields(ScenePlan)}
    exact=[]
    for scene in resolved.get('scenes',[]):
        if scene.get('source_review')=='author-source-v1':
            plan=ScenePlan(**{k:v for k,v in scene.items() if k in names})
            exact.extend(o.source_id for o in build_scene_observations(resolved['id'],plan,[(m['sender'],m['text']) for m in plan.messages]))
    instructions = whole_prompt([], {'checks': []}).split('Required assessment IDs in order:')[0]
    return instructions + (
        'This audit intentionally isolates ONE checkpoint to prevent cross-check source/identity mixing. '
        'Review ALL supplied eligible records and earlier public inputs, but assess only the target ID. '
        'The exact author-owned records are intentionally unjudged writing: assess their question '
        'answerability and coherence, not their prose or compliance with private scene directions. '
        'Generated records remain subject to naturalness review. Do not invent absent quotations or '
        'substitute another speaker. Source citations must use source_record grounding; public-input '
        'citations require explicit_public_report grounding.\n'
        f'Author-owned exact source IDs: {json.dumps(exact)}\n'
        f'Target public input:\n{json.dumps({k:v for k,v in check.items() if k in ("id","at","prompt","session","kind","trigger","depends_on","requires")},ensure_ascii=False)}\n'
        f'Required assessment IDs in order:\n{json.dumps([check["id"]])}\n'
        f'Response schema:\n{json.dumps(response_schema([check["id"]]))}\n'
        f'Complete eligible source records and public inputs:\n{json.dumps(scope,ensure_ascii=False,sort_keys=True)}')

def check_errors(verdict, messages, resolved, check):
    # Validate the one result against the FULL chronological context, so earlier
    # delivered inputs retain their real identity and conditional eligibility.
    rows = verdict.get('assessments', []) if isinstance(verdict, dict) else []
    errors = validation_errors(verdict, messages, resolved)
    errors = [e for e in errors if e != {'path':'assessments','code':'ids_or_order_mismatch'}]
    if not isinstance(rows,list) or [x.get('check_id') if isinstance(x,dict) else None for x in rows] != [check['id']]:
        errors.append({'path':'assessments','code':'target_ids_mismatch'})
    if not isinstance(verdict,dict) or any(verdict.get(k) is not True for k in ('consistent','questions_answerable','natural_enough')) or verdict.get('issues') != []:
        errors.append({'path':'review','code':'negative_verdict'})
    return errors

def review(messages, resolved, model, root, *, locator_binding=False):
    directory = root/'checkpoint-source-reviews'; directory.mkdir(exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor
    def one(check):
        path=directory/(check['id']+'.json')
        if path.exists():
            receipt=json.loads(path.read_text())
            # Reuse only an exact passing receipt; a rejected review is terminal.
            if receipt.get('state')!='pass':
                if not locator_binding:raise ValueError('Rejected checkpoint source audit: '+check['id'])
                bind_receipt(root,messages,resolved,check,receipt,path,write=True)
            return receipt
        result=model(prompt(messages,resolved,check)); verdict=json.loads(result.text)
        errors=check_errors(verdict,messages,resolved,check)
        receipt={'version':VERSION,'check_id':check['id'],'state':'fail' if errors else 'pass',
            'evidence_hash':digest(eligible_evidence(messages,resolved,check['id'])),
            'review':verdict,'validation_errors':errors,'model_call':result.record_path,'usage':result.usage}
        save(path,receipt)
        if errors:
            if locator_binding:bind_receipt(root,messages,resolved,check,receipt,path,write=True)
            else:raise ValueError('Checkpoint source audit failed '+check['id']+': '+str(errors))
        return receipt
    with ThreadPoolExecutor(max_workers=4) as pool: rows=list(pool.map(one,resolved['checks']))
    receipt={'state':'pass','review_version':BOUND_VERSION if locator_binding else VERSION,'source_hash':digest(messages),
        'intent_hash':digest(review_intent(resolved)),'checkpoint_ids':[r['check_id'] for r in rows],
        'scope':'source-coherence-and-public-question-answerability',
        'supersedes':'../preparation-corrected-v3/realism-review.json',
        'derivation':'Checkpoint-isolated eligible-history input replaces demonstrably mixed identities and fabricated citations in the retained whole-corpus output; no verdict edited or rerolled.'}
    save(root/'realism-review.json',receipt)
    return receipt

def audit(root,messages,resolved,quality):
    errors=[]
    version=quality.get('review_version')
    if version not in {VERSION,BOUND_VERSION}:return ['Unknown checkpoint review version']
    expected={'state':'pass','review_version':version,'source_hash':digest(messages),
        'intent_hash':digest(review_intent(resolved)), 'checkpoint_ids':[c['id'] for c in resolved['checks']],
        'scope':'source-coherence-and-public-question-answerability'}
    if any(quality.get(k)!=v for k,v in expected.items()):errors.append('Checkpoint source review aggregate is stale or incomplete')
    for check in resolved['checks']:
        r=json.loads((root/'checkpoint-source-reviews'/(check['id']+'.json')).read_text())
        call=json.loads((root/'model-calls'/Path(r['model_call']).name).read_text())
        bound=version==BOUND_VERSION and r.get('state')=='fail'
        if bound:
            try:bind_receipt(root,messages,resolved,check,r,root/'checkpoint-source-reviews'/(check['id']+'.json'),write=False)
            except (ValueError,OSError,KeyError,TypeError):errors.append('Invalid locator binding: '+check['id'])
        if (r.get('version')!=VERSION or r.get('check_id')!=check['id'] or (r.get('state')!='pass' and not bound) or
            r.get('evidence_hash')!=digest(eligible_evidence(messages,resolved,check['id'])) or
            (not bound and (check_errors(r.get('review'),messages,resolved,check) or r.get('validation_errors')!=[])) or
            call.get('status')!='ok' or json.loads(call.get('text',''))!=r.get('review') or
            call.get('request',{}).get('messages')!=[{'role':'user','content':prompt(messages,resolved,check)}]):
            errors.append('Invalid checkpoint source receipt: '+check['id'])
    return errors


def bind_receipt(root,messages,resolved,check,receipt,path,*,write):
    from .citation_binding import bind
    call_path=root/'model-calls'/Path(receipt['model_call']).name
    view,binding=bind(receipt,messages,resolved,check,path.read_bytes(),call_path.read_bytes())
    target=root/'citation-bindings'/(check['id']+'.json')
    if write:
        target.parent.mkdir(exist_ok=True);save(target,binding)
    elif json.loads(target.read_text())!=binding:raise ValueError('Locator binding changed')
    return view
