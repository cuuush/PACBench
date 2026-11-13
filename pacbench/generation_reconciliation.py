"""Reconcile one proven cancelled direct generation without accepting its reply."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from .direct_accounting import live_budget,money,now,VERSION as DIRECT_PROTOCOL
from .openrouter_route import MODEL

VERSION='cancelled-generation-charge-v1'
ORIGIN='https://openrouter.ai/api/v1/'


def _save(path,data):
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'wb') as stream:stream.write(data)


def _fetch(url,key):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+key})
    with urllib.request.build_opener(NoRedirect).open(request,timeout=30) as response:
        if response.status!=200 or response.geturl()!=url:raise ValueError('Unverified official metadata response')
        data=response.read(16*1024*1024+1)
    if len(data)>16*1024*1024:raise ValueError('Metadata response exceeds bound')
    return data


def _attempt(path):
    attempt=json.loads(path.read_text())
    if (attempt.get('status')!='failed' or attempt.get('url')!=ORIGIN+'chat/completions' or
            attempt.get('request',{}).get('model')!=MODEL or attempt['request'].get('stream') is not True or
            attempt.get('direct_response',{}).get('http_status')!=200):
        raise ValueError('Only a failed direct stream is eligible')
    relative=Path(attempt['stream_wire_response']['relative_path'])
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe original stream path')
    wire=path.parent/relative
    if not wire.resolve().is_relative_to(path.parent.resolve()):raise ValueError('Original stream escapes its run')
    raw=wire.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=attempt['stream_wire_response']['sha256']:raise ValueError('Original stream differs from receipt')
    ids=set();models=set()
    for line in raw.splitlines():
        if not line.startswith(b'data:'):continue
        payload=line[5:].strip()
        if payload==b'[DONE]':raise ValueError('Original stream already has a terminal marker')
        try:event=json.loads(payload)
        except ValueError:continue  # Preserve a truncated last event, never treat it as completion.
        if not isinstance(event,dict):raise ValueError('Unexpected original stream event')
        if event.get('id'):ids.add(event['id'])
        if event.get('model'):models.add(event['model'])
    if len(ids)!=1 or models!={MODEL}:raise ValueError('Original generation identity/model is unproven')
    return attempt,next(iter(ids)),digest


def reconcile_cancelled(attempt_path,evidence_dir,key,*,budget=None):
    """Fetch authentic usage, retain all failure evidence, and settle only this bill.

    No completion text is recovered, no model request is repeated, and no other
    reservation is touched. Oversized reported liabilities are recorded and stop
    further admission; authoritative charges are never discarded to fit a cap.
    """
    budget=budget or live_budget();attempt_path=Path(attempt_path);directory=Path(evidence_dir)
    attempt,identifier,stream_hash=_attempt(attempt_path)
    row=next(r for r in budget.snapshot() if r['id']==attempt['accounting_receipt']['id'])
    row.pop('accounted_upper_bound_usd',None)
    request_hash=hashlib.sha256(json.dumps(attempt['request'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if (row['state']!='uncertain' or row.get('transport')!=DIRECT_PROTOCOL or
            row.get('http_status')!=200 or not row.get('finished') or row.get('served_model')!=MODEL or
            row.get('served_provider')!='DeepInfra' or
            row.get('request_sha256')!=request_hash or
            row.get('upstream_request_id')!=identifier):raise ValueError('Original unresolved accounting identity differs')
    directory.mkdir(parents=True,exist_ok=False,mode=0o700)
    generation_url=ORIGIN+'generation?'+urllib.parse.urlencode({'id':identifier})
    generation_raw=_fetch(generation_url,key);_save(directory/'generation.json',generation_raw)
    catalog_raw=_fetch(ORIGIN+'models',key);_save(directory/'models.json',catalog_raw)
    generation=json.loads(generation_raw,parse_float=Decimal)['data'];catalog=json.loads(catalog_raw)['data']
    aliases=[m for m in catalog if m.get('id')==MODEL]
    if len(aliases)!=1:raise ValueError('Official model alias is not unique')
    if (generation.get('id')!=identifier or generation.get('cancelled') is not True or
            not isinstance(generation.get('model'),str) or not generation['model'] or
            generation.get('provider_name')!=row.get('served_provider') or
            generation.get('model') not in {MODEL,aliases[0].get('canonical_slug')}):
        raise ValueError('Cancellation metadata identity/provider/model differs')
    cost=money(generation['total_cost'])
    inp=generation.get('native_tokens_prompt');out=generation.get('native_tokens_completion')
    if (type(inp) is not int or type(out) is not int or inp<0 or out<0 or
            inp>budget.config['context_tokens'] or out>row['completion_bound_tokens']):
        raise ValueError('Authoritative native token usage is invalid or outside the attempt bounds')
    receipt={'version':VERSION,'lookup_at':now(),'generation_id':identifier,'cancelled':True,
        'generation_url':generation_url,'models_url':ORIGIN+'models','lookup_http_status':200,
        'lookup_authentication':'configured runtime credential; value never retained',
        'generation_sha256':hashlib.sha256(generation_raw).hexdigest(),
        'catalog_sha256':hashlib.sha256(catalog_raw).hexdigest(),
        'original_attempt':str(attempt_path.resolve()),'original_attempt_sha256':hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
        'original_stream_sha256':stream_hash,'ledger_id':row['id'],
        'requested_model':MODEL,'sse_model':row['served_model'],'generation_model':generation['model'],
        'canonical_slug':aliases[0].get('canonical_slug'),'provider':generation['provider_name'],
        'input_tokens':inp,'output_tokens':out,'reported_charge_usd':str(cost),
        'previous_state':row['state'],'previous_accounted_usd':row['accounted'],
        'scope':'Financial reconciliation of a cancelled attempt only; original review remains ungraded'}
    _save(directory/'receipt.json',(json.dumps(receipt,indent=2)+'\n').encode())
    with budget.locked() as state:
        current=next(r for r in state['calls'] if r['id']==row['id'])
        if current!=row:raise ValueError('Original accounting attempt changed during lookup')
        if any(r['id']!=row['id'] and r.get('upstream_request_id')==identifier and r['state']=='settled' for r in state['calls']):
            raise ValueError('Generation already belongs to another settled attempt')
        current.update(state='settled',accounted=str(cost),input_tokens=inp,output_tokens=out,
            error_label='upstream_cancelled_charge_confirmed',cancelled=True,reconciled_at=receipt['lookup_at'])
        current.setdefault('reconciliations',[]).append({**receipt,'receipt_path':str((directory/'receipt.json').resolve())})
        if cost>money(row['reserved']):current['reported_liability_exceeds_bound']=True
    return receipt
