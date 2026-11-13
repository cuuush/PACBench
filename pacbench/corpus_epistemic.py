"""Source-blind response boundaries: uncertainty is evidence, not a blanket pass."""
from __future__ import annotations
from datetime import datetime
import json
from .followups import digest

VERSION='source-realism-v10'
PUBLIC_KEYS=('id','at','prompt','session','kind','trigger','depends_on','requires')
STATES={'determinate','bounded_unknown','clarification_warranted','invalid'}


def response_schema():
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


def prompt(messages,resolved):
    questions=[{**{k:r[k] for k in PUBLIC_KEYS if k in r},
                'eligible_evidence_hash':digest(eligible_evidence(messages,resolved,r['id']))}
               for r in resolved['checks']]
    return (
        'Independently audit this complete raw personal-message corpus and every public question. '
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
        'Return one assessment per question in canonical order. Each has check_id, event_date, '
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
        'Set questions_answerable true only when every question has an assessable source-derived '
        'boundary (determinate, bounded_unknown or clarification_warranted), consistent true only '
        'for coherent source, and natural_enough true only for plausible source. Report genuine '
        'defects in issues; do not list properly bounded unknowns, resolved corrections or old '
        'quotations as defects by themselves. Any invalid assessment or current-state conflict '
        'prevents approval. Return one JSON object conforming to the schema below. Schema fields '
        'describe output types, not desired verdicts or defaults. This review does not replace '
        'independent criterion verification.\n'
        f'Response schema:\n{json.dumps(response_schema())}\n'
        f'Complete raw source records:\n{json.dumps(messages,ensure_ascii=False,sort_keys=True)}\n'
        f'Ordered timestamped public inputs and scope bindings:\n{json.dumps(questions,ensure_ascii=False,sort_keys=True)}')


def validation_errors(verdict,messages,resolved):
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
