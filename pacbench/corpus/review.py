"""Independent realism review bound to the actual frozen source and expectations."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from pacbench.corpus.contract import _dialogue
from pacbench.evidence_store import binding_digest as digest
from pacbench.evidence_store import save

VERSION = 'source-realism-v10'
SOURCE_BLIND = {'source-realism-v7','source-realism-v8','source-realism-v9',VERSION,'source-realism-v11'}
SUPPORTED_VERSIONS = {'source-realism-v3', 'source-realism-v4', 'source-realism-v5', 'source-realism-v6', *SOURCE_BLIND}


def review_intent(resolved):
    return {'scenes': [{k: s[k] for k in ('id', 'at', 'thread', 'scene', 'facts')}
                       for s in resolved['scenes']], 'checks': resolved['checks']}


def review_prompt(messages, resolved, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unsupported corpus review version')
    if version in {'source-realism-v10','source-realism-v11'}:
        if version=='source-realism-v11':
            from dataclasses import fields

            from pacbench.corpus.authoring import ScenePlan, build_scene_observations
            names={f.name for f in fields(ScenePlan)}
            exact_ids=[]
            for s in resolved['scenes']:
                if s.get('source_review')=='author-source-v1':
                    plan=ScenePlan(**{k:v for k,v in s.items() if k in names})
                    exact_ids.extend(o.source_id for o in build_scene_observations(resolved['id'],plan,
                        [(m['sender'],m['text']) for m in plan.messages]))
            return epistemic_prompt(messages,resolved)+ ('\nAuthor-owned exact source IDs: '+json.dumps(exact_ids)+
                '\nThese records are intentionally unjudged author source: do not assess their writing, '
                'naturalness or compliance with scene facts. The natural_enough gate applies only to '
                'generated source. Use ALL actual records for independent question answerability and '
                'source-supported criterion checks; an author-owned record is not proof that an expectation is valid.')
        return epistemic_prompt(messages,resolved)
    if version == 'source-realism-v9':
        return _v8_prompt(messages,resolved) + (
            '\nJudge the intended state assuming memory retained prior delivered '
            'inputs, including across fresh sessions. A definite user report of '
            'a changed plan updates that state without requiring another person '
            'to confirm it. Whether a provider actually saved the update is what '
            'later recall tests; it is not a source-answerability requirement.')
    if version == 'source-realism-v8':return _v8_prompt(messages,resolved)
    if version == 'source-realism-v7':
        return _v7_prompt(messages, resolved)
    if version == 'source-realism-v6':
        return _v6_prompt(messages, resolved)
    return (
        'Audit this synthetic personal-message corpus and its intended checks. '
        'Read the actual messages; authored intentions do not establish facts. '
        'Check chronology, natural relative-day references, cross-thread/person '
        'continuity, and whether each expected answer follows objectively from '
        'the messages available at question time. Distinguish quotes, jokes, '
        'suggestions, rejected changes and confirmed plans. Flag contradictory '
        'or ambiguous ground truth, implausible conversation, and tests of '
        'assistant style rather than memory. Do not demand explicit repetition '
        'where ordinary conversational context resolves a reference. '
        'Return JSON {"consistent":true,"expectations_supported":true,'
        '"natural_enough":true,"issues":[],"current_upcoming":[],"notes":"..."}. '
        'If something is wrong, set the relevant flag false and describe the '
        'issue; do not repair or excuse it to satisfy the authored expectation.\n'
        + ('Scripted public user messages are real planned inputs when delivered: '
           'they may correct a plan or introduce a fact. Distinguish those inputs '
           'from private expectations and invented assistant replies. Honor requires '
           'and depends_on for conditional inputs. A behavior check may accept an '
           'ordinary acknowledgement; only a later fresh-session question tests '
           'persistence. Do not assume the assistant successfully wrote memory.\n'
           if version in ('source-realism-v4', 'source-realism-v5') else '') +
        ('For every relevant relative day or week, use the written timestamp of '
         'the message containing it, including each reply separately. Resolve '
         'rescheduling direction and date from the messages themselves, not '
         'from the authored intention. Reject conflicting dates and references '
         'that leave materially different dates plausible, regardless of the '
         'authored intention. Earlier or quoted '
         'messages retain their own written clock. Explicit dates and ordinary '
         'context may resolve a reference; do not invent an anchor to make it fit.\n'
         if version == 'source-realism-v5' else '') +
        f'Authored intentions and questions:\n{json.dumps(review_intent(resolved), ensure_ascii=False)}\n'
        f'Actual source:\n{_dialogue(messages)}')


def _v6_prompt(messages, resolved):
    from pacbench.workflows.source_plan import daily_batches, document_text
    batches = daily_batches(messages, resolved.get('timezone', 'America/New_York'))
    source = '\n'.join(document_text(batch, max(m['collected_at'] for m in batch))
                       for _, batch in batches)
    return (
        'Independently audit this complete raw message corpus. Read the messages '
        'first and establish what they say before comparing any authored intentions. '
        'Every message body is retained, including noise and quoted email history. '
        'The timestamp beside each message is its own written time. Resolve every '
        'relevant relative day and week using that timestamp, including replies and '
        'quoted messages. Identify materially plausible alternative dates; do not '
        'invent a later or earlier anchor from the author’s expectation. A proposal '
        'to move an event to a weekday can refer to the next occurrence from the '
        'message date or to a weekday in the event’s week unless the source itself '
        'resolves it. Distinguish confirmed plans, quotes, jokes, rejected changes '
        'and tentative ideas. Preserve ordinary conversational context.\n'
        f'Actual source, chronological daily transcripts:\n{source}\n'
        'Now evaluate each check using only messages available at its own time. '
        'Authored scenes, facts and expectations are hypotheses, not evidence or '
        'anchors. Scripted public user turns are real inputs when delivered; '
        'honor requires and depends_on, and never assume assistant memory writes. '
        'Report one assessment per check ID. Each assessment must contain '
        'check_id, event_date (a concrete date or not applicable), source_evidence '
        '(a nonempty array of {written_at, thread, quote} using exact source text), '
        'alternatives (a nonempty array of plausible alternatives or the string '
        'none in an array), and ambiguous (boolean). A check is ambiguous if '
        'materially different dates or outcomes remain plausible. Set consistent '
        'or expectations_supported false for contradictions, unsupported claims '
        'or ambiguity; set natural_enough false for implausible conversation. '
        'Return only JSON: {"consistent":true,"expectations_supported":true,'
        '"natural_enough":true,"issues":[],"assessments":[{"check_id":"...",'
        '"event_date":"...","source_evidence":[{"written_at":"...",'
        '"thread":"...","quote":"..."}],"alternatives":["none"],'
        '"ambiguous":false}],"notes":"..."}. Explain actual issues without '
        'repairing them to satisfy the expectation.\n'
        f'Authored intentions and timestamped questions:\n{json.dumps(review_intent(resolved), ensure_ascii=False)}')


def _v8_prompt(messages,resolved):
    return _v7_prompt(messages,resolved,include_turn_metadata=True) + (
        '\nExact citations may also quote the timestamped public user inputs '
        'above. Later inputs cannot justify earlier answers; conditional inputs '
        'are not established history. User instructions do not prove assistant '
        'persistence or completed external actions.')


def _v7_prompt(messages, resolved, *, include_turn_metadata=False):
    from pacbench.workflows.source_plan import daily_batches, document_text
    batches = daily_batches(messages, resolved.get('timezone', 'America/New_York'))
    source = '\n'.join(document_text(batch, max(m['collected_at'] for m in batch))
                       for _, batch in batches)
    keys=('id','at','prompt','session','kind','trigger','depends_on','requires') if include_turn_metadata else ('id','at','prompt')
    questions = [{k:check[k] for k in keys if k in check} for check in resolved['checks']]
    return (
        'Independently audit this complete raw personal-message corpus and the '
        'answerability of its public questions. Every message body is retained, '
        'including noise and quoted email history. The timestamp beside each '
        'message is its own written time. Resolve relative days and weeks using '
        'that timestamp, including replies and quoted messages. Identify materially '
        'plausible alternative dates and outcomes from the source. Distinguish '
        'confirmed plans, quotes, jokes, rejected changes and tentative ideas. '
        'Preserve ordinary conversational context.\n'
        f'Actual source, chronological daily transcripts:\n{source}\n'
        'For each timestamped question use only information available at its '
        'question time. Evaluate source coherence, naturalness and whether the '
        'question has a determinate answer. Public user questions are actual '
        'inputs when delivered; do not assume assistant memory writes. Report one '
        'assessment per question ID with event_date (a concrete date or not '
        'applicable), source_evidence (a nonempty array of {written_at, quote} '
        'using exact source text and timestamp), alternatives (a nonempty array '
        'of plausible alternatives or the string none in an array), and '
        'ambiguous (boolean). Set consistent false for contradictions, '
        'questions_answerable false for material ambiguity or unsupported answers, '
        'and natural_enough false for implausible conversation. Return only JSON: '
        '{"consistent":true,"questions_answerable":true,"natural_enough":true,'
        '"issues":[],"assessments":[{"check_id":"...","event_date":"...",'
        '"source_evidence":[{"written_at":"...","quote":"..."}],'
        '"alternatives":["none"],"ambiguous":false}],"notes":"..."}. '
        'Explain any issue.\n'
        f'Timestamped public questions:\n{json.dumps(questions, ensure_ascii=False)}')


def validation_errors(verdict, messages, resolved, *, version=VERSION):
    """Verify exact citations; v8 also accepts delivered public user inputs."""
    if version in {'source-realism-v10','source-realism-v11'}:
        return epistemic_errors(verdict,messages,resolved)
    errors = []
    def add(path, code):
        errors.append({'path': path, 'code': code})
    if not isinstance(verdict, dict):
        return [{'path': 'review', 'code': 'not_object'}]
    checks = resolved['checks']
    candidates=list(messages)
    if version == 'source-realism-v8':
        candidates.extend({'written_at':r['at'],'collected_at':r['at'],'text':r['prompt']}
                          for r in checks if r.get('trigger') != 'clarification')
    conditional={r['id'] for r in checks if r.get('trigger')=='clarification'}
    for r in checks:
        dependencies=[*r.get('requires',[]),*([r['depends_on']] if r.get('depends_on') else [])]
        if any(d in conditional for d in dependencies):conditional.add(r['id'])
    ids = [c['id'] for c in checks]
    if len(set(ids)) != len(ids): add('checks', 'duplicate_id')
    by_id = {c['id']: c for c in checks}
    assessments = verdict.get('assessments')
    if not isinstance(assessments, list):
        add('assessments', 'not_array')
        return errors
    if len(assessments) != len(checks): add('assessments', 'wrong_count')
    from datetime import datetime
    found = []
    for index, item in enumerate(assessments):
        if not isinstance(item, dict):
            add(f'assessments[{index}]', 'not_object')
            continue
        check_id = item.get('check_id')
        path = f'assessments[{check_id}]' if isinstance(check_id, str) else f'assessments[{index}]'
        if isinstance(check_id, str): found.append(check_id)
        if not isinstance(check_id, str) or check_id not in by_id:
            add(path + '.check_id', 'unknown_id')
        if not isinstance(item.get('event_date'), str) or not item['event_date'].strip():
            add(path + '.event_date', 'missing_text')
        if item.get('ambiguous') is not False:
            add(path + '.ambiguous', 'not_false')
        alternatives = item.get('alternatives')
        if not isinstance(alternatives, list) or not alternatives or not all(isinstance(x, str) and x.strip() for x in alternatives):
            add(path + '.alternatives', 'invalid_array')
        evidence = item.get('source_evidence')
        if not isinstance(evidence, list) or not evidence:
            add(path + '.source_evidence', 'invalid_array')
            continue
        for i, entry in enumerate(evidence):
            evidence_path = f'{path}.source_evidence[{i}]'
            if not isinstance(entry, dict) or not isinstance(entry.get('quote'), str) or not entry['quote'].strip():
                add(evidence_path, 'missing_quote')
                continue
            available=candidates
            if version == 'source-realism-v9':
                prior=checks[:next((i+1 for i,c in enumerate(checks) if c['id']==check_id),0)]
                available=[*messages,*({'written_at':r['at'],'collected_at':r['at'],'text':r['prompt']}
                    for r in prior if r['id'] not in conditional)]
            matches = [m for m in available if entry.get('written_at') == m['written_at'] and entry['quote'] in m['text']]
            if not matches:
                add(evidence_path, 'timestamp_quote_no_match')
            elif len(matches) > 1:
                add(evidence_path, 'timestamp_quote_ambiguous_match')
            elif isinstance(check_id, str) and check_id in by_id and datetime.fromisoformat(matches[0]['collected_at']) > datetime.fromisoformat(by_id[check_id]['at']):
                add(evidence_path, 'source_after_question')
    if len(found) != len(ids) or len(set(found)) != len(found) or set(found) != set(ids):
        add('assessments', 'ids_mismatch')
    return errors


def verdict_passes(verdict, messages, resolved, *, version=VERSION):
    flag = 'questions_answerable' if version in SOURCE_BLIND else 'expectations_supported'
    if not (isinstance(verdict, dict) and
            all(verdict.get(k) is True for k in
                ('consistent', flag, 'natural_enough')) and
            verdict.get('issues') == []):
        return False
    if version in SOURCE_BLIND:
        return not validation_errors(verdict, messages, resolved,version=version)
    if version != 'source-realism-v6':
        return True
    assessments = verdict.get('assessments')
    checks = resolved['checks']
    if not isinstance(assessments, list) or len(assessments) != len(checks):
        return False
    ids = [c['id'] for c in checks]
    if len(set(ids)) != len(ids):
        return False
    from datetime import datetime
    by_id = {c['id']: c for c in checks}
    found = []
    for item in assessments:
        if not isinstance(item, dict): return False
        found.append(item.get('check_id'))
        if item.get('check_id') not in by_id: return False
        if (not isinstance(item.get('event_date'), str) or not item['event_date'].strip()
                or item.get('ambiguous') is not False): return False
        alternatives = item.get('alternatives')
        if not isinstance(alternatives, list) or not alternatives or not all(isinstance(x, str) and x.strip() for x in alternatives): return False
        evidence = item.get('source_evidence')
        if not isinstance(evidence, list) or not evidence: return False
        for e in evidence:
            if not isinstance(e, dict) or not isinstance(e.get('quote'), str) or not e['quote'].strip(): return False
            matches = [m for m in messages if e.get('written_at') == m['written_at']
                       and e['quote'] in m['text']]
            if version == 'source-realism-v6':
                matches = [m for m in matches if e.get('thread') == m['thread']]
                if not matches: return False
            elif len(matches) != 1:
                return False
            if not any(datetime.fromisoformat(m['collected_at']) <=
                       datetime.fromisoformat(by_id[item['check_id']]['at']) for m in matches): return False
    return len(set(found)) == len(found) and set(found) == set(ids)


def review_corpus(messages, resolved, model, root: Path, *, version=VERSION):
    # ModelClient reuses only an exact successful request. A changed source or
    # expectation produces a new request, including on preparation resume.
    from pacbench.model_calls import ModelClient
    # Full-corpus output is larger than a per-answer judgment. This changes only
    # the client deadline, keeping the actual model request and review standard.
    if isinstance(model,ModelClient) and len(resolved['checks'])>8:
        import copy
        model=copy.copy(model);model.timeout_s=900;model.max_attempts=1
    result = model(review_prompt(messages, resolved,version=version))
    verdict = json.loads(result.text)
    passed = verdict_passes(verdict, messages, resolved,version=version)
    receipt = {'state': 'pass' if passed else 'fail', 'review_version': version,
               'source_hash': digest(messages), 'intent_hash': digest(review_intent(resolved)),
               'review': verdict, 'model_call': result.record_path, 'usage': result.usage}
    if isinstance(model,ModelClient):
        from pacbench.evidence_store import read_json
        call=read_json(Path(result.record_path))
        deadline=call.get('client_wall_deadline_s',call.get('direct_timing',{}).get('wall_deadline_s'))
        if deadline is not None:
            receipt['execution_condition']={'version':'corpus-review-real-deadline-v1','wall_deadline_s':deadline}
            if 'client_max_attempts' in call:receipt['execution_condition']['max_attempts']=call['client_max_attempts']
    if version in SOURCE_BLIND:
        receipt['scope'] = 'source-coherence-and-public-question-answerability'
        receipt['validation_errors'] = validation_errors(verdict, messages, resolved,version=version)
    if version in {'source-realism-v10','source-realism-v11'}:
        receipt['eligibility_bindings']={c['id']:digest(eligible_evidence(messages,resolved,c['id'])) for c in resolved['checks']}
    save(root / 'realism-review.json', receipt)
    if not passed:
        raise ValueError(f'Corpus realism review rejected: {verdict}')
    return receipt


CHECKPOINT_VERSION = 'source-realism-v12-checkpoint-isolated'
BOUND_CHECKPOINT_VERSION = 'source-realism-v13-checkpoint-locator-bound'

def checkpoint_prompt(messages, resolved, check):
    scope = eligible_evidence(messages, resolved, check['id'])
    from dataclasses import fields

    from pacbench.corpus.authoring import ScenePlan, build_scene_observations
    names={f.name for f in fields(ScenePlan)}
    exact=[]
    for scene in resolved.get('scenes',[]):
        if scene.get('source_review')=='author-source-v1':
            plan=ScenePlan(**{k:v for k,v in scene.items() if k in names})
            exact.extend(o.source_id for o in build_scene_observations(resolved['id'],plan,[(m['sender'],m['text']) for m in plan.messages]))
    instructions = epistemic_prompt([], {'checks': []}).split('Required assessment IDs in order:')[0]
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

def checkpoint_errors(verdict, messages, resolved, check):
    # Validate the one result against the FULL chronological context, so earlier
    # delivered inputs retain their real identity and conditional eligibility.
    rows = verdict.get('assessments', []) if isinstance(verdict, dict) else []
    errors = epistemic_errors(verdict, messages, resolved)
    errors = [e for e in errors if e != {'path':'assessments','code':'ids_or_order_mismatch'}]
    if not isinstance(rows,list) or [x.get('check_id') if isinstance(x,dict) else None for x in rows] != [check['id']]:
        errors.append({'path':'assessments','code':'target_ids_mismatch'})
    if not isinstance(verdict,dict) or any(verdict.get(k) is not True for k in ('consistent','questions_answerable','natural_enough')) or verdict.get('issues') != []:
        errors.append({'path':'review','code':'negative_verdict'})
    return errors

def review_checkpoints(messages, resolved, model, root, *, locator_binding=False):
    directory = root/'checkpoint-source-reviews'; directory.mkdir(exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor
    def one(check):
        path=directory/(check['id']+'.json')
        if path.exists():
            receipt=json.loads(path.read_text())
            # Reuse only an exact passing receipt; a rejected review is terminal.
            if receipt.get('state')!='pass':
                if not locator_binding:raise ValueError('Rejected checkpoint source audit: '+check['id'])
                bind_checkpoint_receipt(root,messages,resolved,check,receipt,path,write=True)
            return receipt
        result=model(checkpoint_prompt(messages,resolved,check)); verdict=json.loads(result.text)
        errors=checkpoint_errors(verdict,messages,resolved,check)
        receipt={'version':CHECKPOINT_VERSION,'check_id':check['id'],'state':'fail' if errors else 'pass',
            'evidence_hash':digest(eligible_evidence(messages,resolved,check['id'])),
            'review':verdict,'validation_errors':errors,'model_call':result.record_path,'usage':result.usage}
        save(path,receipt)
        if errors:
            if locator_binding:bind_checkpoint_receipt(root,messages,resolved,check,receipt,path,write=True)
            else:raise ValueError('Checkpoint source audit failed '+check['id']+': '+str(errors))
        return receipt
    with ThreadPoolExecutor(max_workers=4) as pool: rows=list(pool.map(one,resolved['checks']))
    receipt={'state':'pass','review_version':BOUND_CHECKPOINT_VERSION if locator_binding else CHECKPOINT_VERSION,'source_hash':digest(messages),
        'intent_hash':digest(review_intent(resolved)),'checkpoint_ids':[r['check_id'] for r in rows],
        'scope':'source-coherence-and-public-question-answerability',
        'supersedes':'../preparation-corrected-v3/realism-review.json',
        'derivation':'Checkpoint-isolated eligible-history input replaces demonstrably mixed identities and fabricated citations in the retained whole-corpus output; no verdict edited or rerolled.'}
    save(root/'realism-review.json',receipt)
    return receipt

def audit_checkpoints(root,messages,resolved,quality):
    errors=[]
    version=quality.get('review_version')
    if version not in {CHECKPOINT_VERSION,BOUND_CHECKPOINT_VERSION}:return ['Unknown checkpoint review version']
    expected={'state':'pass','review_version':version,'source_hash':digest(messages),
        'intent_hash':digest(review_intent(resolved)), 'checkpoint_ids':[c['id'] for c in resolved['checks']],
        'scope':'source-coherence-and-public-question-answerability'}
    if any(quality.get(k)!=v for k,v in expected.items()):errors.append('Checkpoint source review aggregate is stale or incomplete')
    for check in resolved['checks']:
        r=json.loads((root/'checkpoint-source-reviews'/(check['id']+'.json')).read_text())
        call=json.loads((root/'model-calls'/Path(r['model_call']).name).read_text())
        bound=version==BOUND_CHECKPOINT_VERSION and r.get('state')=='fail'
        if bound:
            try:bind_checkpoint_receipt(root,messages,resolved,check,r,root/'checkpoint-source-reviews'/(check['id']+'.json'),write=False)
            except (ValueError,OSError,KeyError,TypeError):errors.append('Invalid locator binding: '+check['id'])
        if (r.get('version')!=CHECKPOINT_VERSION or r.get('check_id')!=check['id'] or (r.get('state')!='pass' and not bound) or
            r.get('evidence_hash')!=digest(eligible_evidence(messages,resolved,check['id'])) or
            (not bound and (checkpoint_errors(r.get('review'),messages,resolved,check) or r.get('validation_errors')!=[])) or
            call.get('status')!='ok' or json.loads(call.get('text',''))!=r.get('review') or
            call.get('request',{}).get('messages')!=[{'role':'user','content':checkpoint_prompt(messages,resolved,check)}]):
            errors.append('Invalid checkpoint source receipt: '+check['id'])
    return errors


def bind_checkpoint_receipt(root,messages,resolved,check,receipt,path,*,write):
    call_path=root/'model-calls'/Path(receipt['model_call']).name
    view,binding=bind_citation_locators(receipt,messages,resolved,check,path.read_bytes(),call_path.read_bytes())
    target=root/'citation-bindings'/(check['id']+'.json')
    if write:
        target.parent.mkdir(exist_ok=True);save(target,binding)
    elif json.loads(target.read_text())!=binding:raise ValueError('Locator binding changed')
    return view


PUBLIC_KEYS=('id','at','prompt','session','kind','trigger','depends_on','requires')
STATES={'determinate','bounded_unknown','clarification_warranted','invalid'}


def response_schema(assessment_ids=None):
    text={'type':'string','minLength':1};texts={'type':'array','items':text,'minItems':1}
    citation={'type':'object','required':['source_id','written_at','quote'],
        'properties':{key:text for key in ['source_id','written_at','quote']}}
    unresolved={'type':'object','required':['attribute','missing','alternatives'],
        'properties':{'attribute':text,'missing':text,'alternatives':texts}}
    properties={'check_id':text,'event_date':text,'answerability':{'enum':sorted(STATES)},
        'grounding_kind':{'enum':['source_record','explicit_public_report','presupposition_only','absent']},
        **{k:{'type':'boolean'} for k in ['assessable','ambiguous','current_state_conflict','full_eligible_history_reviewed']},
        'known_fields':{'type':'array','items':text},'unresolved_fields':{'type':'array','items':unresolved},
        'reason':text,'alternatives':texts,
        'source_evidence':{'type':'array','items':citation}}
    top={**{k:{'type':'boolean'} for k in ['consistent','questions_answerable','natural_enough']},
        'issues':{'type':'array','items':text},'notes':text,
        'assessments':{'type':'array','items':{'type':'object','required':list(properties),'properties':properties}}}
    if assessment_ids is not None:
        top['assessments'].update(minItems=len(assessment_ids),maxItems=len(assessment_ids))
    return {'type':'object','required':list(top),'properties':top}


def eligible_evidence(messages,resolved,check_id):
    checks=resolved['checks'];index=next(i for i,c in enumerate(checks) if c['id']==check_id)
    check=checks[index];cutoff=datetime.fromisoformat(check['at'])
    conditional=set()
    for row in checks:
        required=[*row.get('requires',[]),*([row['depends_on']] if row.get('depends_on') else [])]
        if row.get('trigger')=='clarification' or any(r in conditional for r in required):conditional.add(row['id'])
    source=[m for m in messages if datetime.fromisoformat(m['collected_at'])<=cutoff and
            datetime.fromisoformat(m['written_at'])<=cutoff]
    public=[{k:r[k] for k in PUBLIC_KEYS if k in r} for r in checks[:index+1]
            if r['id'] not in conditional and datetime.fromisoformat(r['at'])<=cutoff]
    return {'source_records':source,'public_inputs':public}


def epistemic_prompt(messages,resolved):
    questions=[{**{k:r[k] for k in PUBLIC_KEYS if k in r},
                'eligible_evidence_hash':digest(eligible_evidence(messages,resolved,r['id']))}
               for r in resolved['checks']]
    return (
        'Independently audit this complete raw personal-message corpus and EVERY public check/input ID. '
        'Some inputs are declarative reports or instructions rather than grammatical questions. '
        'Assess every supplied ID, including behavior/report turns; do not omit them. '
        'Identifiers are labels, never answer hints or evidence; derive classifications from eligible text. '
        'No authored answer keys or expectations are provided. Read actual source identities, bodies, '
        'written and collection times, quoted history, and ordered public inputs. Source after a '
        'question’s collection cutoff cannot support that answer. Resolve each relative date from '
        'its own written context. Both written and collection times must be no later than the checkpoint. '
        'Public questions’ presuppositions do not independently establish facts, including in earlier questions. '
        'Conditional replies and their dependent inputs are not established history. Never assume '
        'the assistant saved an update or completed an external action.\n'
        'For intended answerability, assume prior delivered information is retained across fresh '
        'sessions, while resolving conversational references in their actual session. A definite '
        'public user report/correction updates intended knowledge without another person’s confirmation. '
        'Actual provider persistence is tested later and is not a source-answerability requirement.\n'
        'Coherence and naturalness are mandatory separate gates. Reject unresolved contradictory '
        'current source assertions or implausible source, even if an answer could say unknown. '
        'Explicit corrections, rejected proposals, clearly attributed disagreements, and explicitly '
        'obsolete quotations are not unresolved contradictions.\n'
        'Assess the source-supported response boundary, not whether the underlying world state is '
        'fully known. Use answerability determinate, bounded_unknown, clarification_warranted, or invalid. '
        'Determinate means the requested attribute has an ordinary supported answer. Bounded unknown '
        'means a grounded event/entity and requested attribute exist, but its value is not established '
        'in the complete eligible history. Clarification warranted means a grounded reference has '
        'multiple material interpretations; identify alternatives and the missing discriminator. '
        'Invalid means there is no grounded objectively assessable response boundary, inaccessible '
        'information is required as a definite fact, or unresolved source conflict prevents a valid test. '
        'Saying unknown is not a universal escape: an entirely absent event or unresolvable target '
        'is invalid. Do not overlook or erase information the source DOES establish.\n'
        'Handle mixed questions by attribute: keep known details in known_fields and identify each '
        'unresolved attribute in unresolved_fields. Classify requested attributes: an explicitly recorded '
        'invitation status can be determinate; attendance without an outcome report is bounded unknown. '
        'If any requested attribute is unresolved, classify the mixed question as bounded_unknown or '
        'clarification_warranted while preserving its other known attributes. A booking/acknowledgement is not attendance; '
        'an invitation is not acceptance; a promise is not completed work; an unexplained name '
        'expansion is not known. Inspect the complete eligible history before concluding a requested '
        'value is absent, and identify that unresolved attribute explicitly.\n'
        'Return exactly one assessment per supplied public check/input ID in canonical order, '
        'including declarative behavior/report turns. Each has check_id, event_date, '
        'answerability, assessable (boolean), ambiguous (descriptive boolean), current_state_conflict '
        '(boolean), known_fields (strings; nonempty for assessable questions), unresolved_fields (array of {attribute, missing, '
        'alternatives:[strings]}), reason (nonempty), full_eligible_history_reviewed:true, '
        'grounding_kind (source_record, explicit_public_report, presupposition_only or absent), alternatives (nonempty strings '
        'or ["none"]), and source_evidence:[{source_id,written_at,quote}]. Use exact nonempty '
        'contiguous source quotes. Cite raw source_id or public-input:<question-id>; a question '
        'alone does not establish its own event. Only a source_record or explicit_public_report '
        'can ground an assessable response; an interrogative presupposition is not a report. '
        'The harness binds eligible-evidence hashes to the request and receipt; do not copy opaque '
        'hashes into your output. For unresolved fields, citations must ground the '
        'referent, and the reason must explain the missing information after examining ALL eligible '
        'history. Known fields must remain responsive and source-supported; do not substitute '
        'blanket uncertainty for resolvable facts.\n'
        'Set questions_answerable true only when every public check/input has an assessable source-derived '
        'boundary (determinate, bounded_unknown or clarification_warranted), consistent true only '
        'for coherent source, and natural_enough true only for plausible source. Report genuine '
        'defects in issues; do not list properly bounded unknowns, resolved corrections or old '
        'quotations as defects by themselves. Any invalid assessment or current-state conflict '
        'prevents approval. Return one JSON object conforming to the schema below. Schema fields '
        'describe output types, not desired verdicts or defaults. This review does not replace '
        'independent criterion verification.\n'
        f'Required assessment IDs in order:\n{json.dumps([c["id"] for c in resolved["checks"]])}\n'
        f'Response schema:\n{json.dumps(response_schema([c["id"] for c in resolved["checks"]]))}\n'
        f'Complete raw source records:\n{json.dumps(messages,ensure_ascii=False,sort_keys=True)}\n'
        f'Ordered timestamped public inputs and scope bindings:\n{json.dumps(questions,ensure_ascii=False,sort_keys=True)}')


def epistemic_errors(verdict,messages,resolved):
    errors=[]
    def add(path,code):errors.append({'path':path,'code':code})
    if not isinstance(verdict,dict):return [{'path':'review','code':'not_object'}]
    checks=resolved['checks'];ids=[c['id'] for c in checks]
    if len(set(ids))!=len(ids):add('checks','duplicate_id')
    if len({m['source_id'] for m in messages})!=len(messages):add('source','duplicate_id')
    rows=verdict.get('assessments')
    if not isinstance(rows,list):return [{'path':'assessments','code':'not_array'}]
    if [r.get('check_id') if isinstance(r,dict) else None for r in rows]!=ids:add('assessments','ids_or_order_mismatch')
    by_id={c['id']:c for c in checks}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('check_id'),str) or row['check_id'] not in by_id:
            add('assessment','unknown_id');continue
        key=row['check_id'];path=f'assessments[{key}]';scope=eligible_evidence(messages,resolved,key)
        if row.get('full_eligible_history_reviewed') is not True:add(path,'full_history_unverified')
        state=row.get('answerability');unknown=isinstance(state,str) and state in {'bounded_unknown','clarification_warranted'}
        if not isinstance(state,str) or state not in STATES or state=='invalid' or row.get('assessable') is not True:add(path,'response_boundary_unassessable')
        if type(row.get('ambiguous')) is not bool:add(path+'.ambiguous','not_boolean')
        if row.get('current_state_conflict') is not False:add(path,'unresolved_source_conflict')
        if not isinstance(row.get('event_date'),str) or not row['event_date'].strip():add(path+'.event_date','missing_text')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():add(path+'.reason','missing_text')
        known=row.get('known_fields')
        if not isinstance(known,list) or not known and state!='invalid' or isinstance(known,list) and any(not isinstance(x,str) or not x.strip() for x in known):add(path+'.known_fields','ungrounded_or_blank')
        unresolved=row.get('unresolved_fields')
        if not isinstance(unresolved,list):add(path+'.unresolved_fields','not_array');unresolved=[]
        if unknown and not unresolved:add(path+'.unresolved_fields','missing_uncertainty_boundary')
        if state=='determinate' and (unresolved or row.get('ambiguous') is True):add(path,'determinate_boundary_inconsistent')
        for item in unresolved:
            if (not isinstance(item,dict) or any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('attribute','missing')) or
                not isinstance(item.get('alternatives'),list) or not item['alternatives'] or
                any(not isinstance(x,str) or not x.strip() for x in item['alternatives'])):
                add(path+'.unresolved_fields','malformed_boundary')
        if state=='clarification_warranted' and (row.get('ambiguous') is not True or not any(
                isinstance(u,dict) and isinstance(u.get('alternatives'),list) and
                len({x.strip().casefold() for x in u['alternatives'] if isinstance(x,str) and x.strip()})>=2 for u in unresolved)):
            add(path,'clarification_alternatives_unproven')
        alternatives=row.get('alternatives')
        if not isinstance(alternatives,list) or not alternatives or any(not isinstance(x,str) or not x.strip() for x in alternatives):add(path+'.alternatives','invalid_array')
        candidates={m['source_id']:{'written_at':m['written_at'],'text':m['text']} for m in scope['source_records']}
        candidates.update({'public-input:'+r['id']:{'written_at':r['at'],'text':r['prompt']} for r in scope['public_inputs']})
        evidence=row.get('source_evidence');grounded=False;grounding=row.get('grounding_kind')
        if grounding not in ('source_record','explicit_public_report'):add(path,'referent_not_factually_grounded')
        if not isinstance(evidence,list) or not evidence and state!='invalid':add(path+'.source_evidence','invalid_array');continue
        if not evidence:continue
        for i,item in enumerate(evidence):
            cite=path+f'.source_evidence[{i}]'
            if not isinstance(item,dict):add(cite,'malformed');continue
            source=candidates.get(item.get('source_id')) if isinstance(item.get('source_id'),str) else None
            if (source is None or item.get('written_at')!=source['written_at'] or
                    not isinstance(item.get('quote'),str) or not item['quote'].strip() or item['quote'] not in source['text']):
                add(cite,'ineligible_or_inexact_quote');continue
            public=item['source_id'].startswith('public-input:')
            grounded=grounded or (grounding=='source_record' and not public or grounding=='explicit_public_report' and public)
        if not grounded:add(path,'no_independent_referent_grounding')
    return errors


CITATION_BINDING_VERSION='missing-final-source-id-character-v1'

def bind_citation_locators(receipt,messages,resolved,check,raw_receipt_bytes,raw_call_bytes):
    raw=receipt['review'];errors=checkpoint_errors(raw,messages,resolved,check)
    if not errors or receipt.get('state')!='fail' or receipt.get('validation_errors')!=errors:
        raise ValueError('Binding requires a retained failed locator receipt')
    if any(e['code']!='ineligible_or_inexact_quote' or not re.fullmatch(r'assessments\['+re.escape(check['id'])+r'\]\.source_evidence\[\d+\]',e['path']) for e in errors):
        raise ValueError('Only locator validation errors can be bound')
    scope=eligible_evidence(messages,resolved,check['id']);eligible=scope['source_records']
    view=deepcopy(raw);bindings=[]
    for error in errors:
        i=int(re.search(r'\[(\d+)\]$',error['path'])[1]);cite=view['assessments'][0]['source_evidence'][i]
        identifier=cite.get('source_id');quote=cite.get('quote');at=cite.get('written_at')
        if not isinstance(identifier,str) or any(m['source_id']==identifier for m in messages):raise ValueError('Existing or invalid locator cannot be rebound')
        matches=[m for m in eligible if isinstance(quote,str) and quote.strip() and quote in m['text'] and at==m['written_at']]
        if len(matches)!=1 or matches[0]['source_id'][:-1]!=identifier or not identifier.startswith('message-'):
            raise ValueError('Locator defect does not have the demonstrated exact unique binding')
        match=matches[0]
        bindings.append({'citation_index':i,'original_id':identifier,'canonical_id':match['source_id'],
            'source_identity_hash':digest(match),'written_at':at,'quote':quote})
        cite['source_id']=match['source_id']
    if checkpoint_errors(view,messages,resolved,check):raise ValueError('Binding leaves validation errors')
    return view,{'version':CITATION_BINDING_VERSION,'check_id':check['id'],'evidence_hash':digest(scope),'bindings':bindings,
        'raw_receipt_sha256':hashlib.sha256(raw_receipt_bytes).hexdigest(),
        'raw_call_sha256':hashlib.sha256(raw_call_bytes).hexdigest(),
        'scope':'Locator-only derived validation view. Raw semantic output and failed receipt are unchanged.'}
