"""Audited correction of the demonstrated missing-final-character source locator."""
import copy,hashlib,json,re
from .followups import digest
from .corpus_epistemic import eligible_evidence
VERSION='missing-final-source-id-character-v1'

def bind(receipt,messages,resolved,check,raw_receipt_bytes,raw_call_bytes):
    from .checkpoint_corpus_review import check_errors
    raw=receipt['review'];errors=check_errors(raw,messages,resolved,check)
    if not errors or receipt.get('state')!='fail' or receipt.get('validation_errors')!=errors:
        raise ValueError('Binding requires a retained failed locator receipt')
    if any(e['code']!='ineligible_or_inexact_quote' or not re.fullmatch(r'assessments\['+re.escape(check['id'])+r'\]\.source_evidence\[\d+\]',e['path']) for e in errors):
        raise ValueError('Only locator validation errors can be bound')
    scope=eligible_evidence(messages,resolved,check['id']);eligible=scope['source_records']
    view=copy.deepcopy(raw);bindings=[]
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
    if check_errors(view,messages,resolved,check):raise ValueError('Binding leaves validation errors')
    return view,{'version':VERSION,'check_id':check['id'],'evidence_hash':digest(scope),'bindings':bindings,
        'raw_receipt_sha256':hashlib.sha256(raw_receipt_bytes).hexdigest(),
        'raw_call_sha256':hashlib.sha256(raw_call_bytes).hexdigest(),
        'scope':'Locator-only derived validation view. Raw semantic output and failed receipt are unchanged.'}
