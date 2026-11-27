"""Offline, Decimal-only receipt accounting. Accounting state is not billing proof."""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from pacbench.openrouter_route import MODEL

SOURCES = {'openrouter.usage.cost', 'openrouter.generation.total_cost',
           'openrouter_generation_total_cost'}


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def usd(value):
    return format(value, 'f') if value is not None else None


def cents(value):
    return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f') if value is not None else None


def safe_response_metadata(value=None, headers=None):
    """Normalize an individual SSE event/header without retaining content or raw payloads."""
    value = value if isinstance(value, dict) else {}
    header_ids = [v for k, v in (headers or {}).items() if str(k).lower() == 'x-generation-id']
    ids = [v for v in [value.get('id'), *header_ids]
           if isinstance(v, str) and re.fullmatch(r'gen-[A-Za-z0-9_-]{1,200}', v)]
    result = {'generation_ids': sorted(set(ids)), 'cost_receipts': []}
    usage = value.get('usage')
    charge = amount(usage.get('cost')) if isinstance(usage, dict) else None
    if charge is not None and ids:
        result['cost_receipts'].append({'source': 'openrouter.usage.cost', 'generation_id': ids[0], 'usd': usd(charge)})
    return result


def persist_metadata(ledger, request_id, metadata):
    """Append immutable safe receipt sidecars; analytics never rewrites the billing ledger."""
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', request_id):
        raise ValueError('Invalid request ID')
    folder = Path(ledger).parent / 'receipt-metadata' / request_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps({'request_id': request_id, **metadata}, sort_keys=True).encode()
    destination = folder / (hashlib.sha256(encoded).hexdigest() + '.json')
    if destination.exists() and destination.read_bytes() == encoded:
        return
    # File contents are durable before the name becomes visible; concurrent
    # duplicate observations cannot replace each other or historical evidence.
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix='.pending-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != encoded:
                raise ValueError('Receipt metadata fingerprint conflict')
        for parent in (folder, folder.parent, folder.parent.parent):
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        os.unlink(temporary)


def retained_metadata(ledger, row):
    """Best effort read of immutable sidecars, with explicit failure coverage."""
    result = dict(row)
    receipts = list(row.get('cost_receipts', []))
    identifiers = list(row.get('receipt_generation_ids', []))
    folder = Path(ledger).parent / 'receipt-metadata' / row['id']
    try:
        for path in sorted(folder.glob('*.json')):
            encoded = path.read_bytes()
            if hashlib.sha256(encoded).hexdigest() != path.stem:
                raise ValueError('Receipt metadata fingerprint mismatch')
            value = json.loads(encoded)
            if value['request_id'] != row['id']:
                raise ValueError('Receipt metadata identity mismatch')
            for item in value['cost_receipts']:
                if item not in receipts:
                    receipts.append(item)
            for identifier in value['generation_ids']:
                if identifier not in identifiers:
                    identifiers.append(identifier)
    except (OSError, ValueError, KeyError, TypeError):
        result['receipt_metadata_incomplete'] = True
    result.update(cost_receipts=receipts, receipt_generation_ids=identifiers)
    return result


def direct_evidence(ledger, row):
    """Verify hash-bound billing evidence and retain explicit metadata errors."""
    ledger = Path(ledger)
    result = retained_metadata(ledger, row)
    errors = []
    evidence = row.get('billing_receipt')
    if evidence:
        try:
            path = ledger.parent / evidence['relative_path']
            if (path.is_symlink() or not path.resolve().is_relative_to(ledger.parent.resolve())):
                raise ValueError('Receipt path outside ledger evidence')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != evidence['sha256']:
                raise ValueError('Receipt hash mismatch')
            receipt = json.loads(raw)
            if (receipt['protocol'] != 'openrouter-response-billing-v1' or
                    receipt['attempt_id'] != row['id'] or receipt['http_status'] != 200 or
                    receipt['cost_source'] != 'response.usage.cost'):
                raise ValueError('Receipt identity/source mismatch')
            metadata = safe_response_metadata({'id': receipt.get('generation_id'),
                'usage': receipt.get('usage')})
            for item in metadata['cost_receipts']:
                if item not in result['cost_receipts']:
                    result['cost_receipts'].append(item)
            for identifier in metadata['generation_ids']:
                if identifier not in result['receipt_generation_ids']:
                    result['receipt_generation_ids'].append(identifier)
            result['direct_receipt_body_complete'] = receipt['body_complete']
            result['served_provider'] = receipt.get('provider')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(type(exc).__name__)
    if row.get('receipt_metadata_error'):
        errors.append(row['receipt_metadata_error'])
    if errors:
        result['receipt_metadata_incomplete'] = True
        result['direct_receipt_evidence_errors'] = errors
    return result

def receipt_evidence(row):
    """Return allowlisted receipt fields only; retain disagreements, never resolve them by order."""
    identities = {v for v in (row.get('generation_id'), row.get('upstream_request_id'),
                  row.get('response_diagnostic_headers', {}).get('x-generation-id'),
                  *row.get('receipt_generation_ids', [])) if v}
    receipts = []
    def add(source, value, identifier):
        cost = amount(value)
        if source in SOURCES and cost is not None and isinstance(identifier, str) and re.fullmatch(r'gen-[A-Za-z0-9_-]{1,200}', identifier):
            item = {'source': source, 'generation_id': identifier, 'usd': usd(cost)}
            if item not in receipts:
                receipts.append(item)
    for receipt in row.get('cost_receipts', []):
        add(receipt.get('source'), receipt.get('usd'), receipt.get('generation_id'))
    if row.get('actual_cost_source') in SOURCES:
        identifier = next(iter(identities)) if len(identities) == 1 else None
        add(row['actual_cost_source'], row.get('actual_cost_usd_decimal'), identifier)
    generation = row.get('generation_receipt', {})
    # A generation lookup must match the recorded response identity, even when delayed.
    if (len(identities) == 1 and generation.get('id') in identities and
            row.get('generation_receipt_source') == 'openrouter.generation.total_cost'):
        add('openrouter.generation.total_cost', generation.get('total_cost'), generation['id'])
    return receipts, identities


def route_evidence(row, receipts, identities):
    """A legacy origin label is a claim, not proof of a configured upstream."""
    evidence = []
    if row.get('transport') == 'direct-client-reservations-v1':
        evidence.append('fixed-openrouter-direct-transport')
    if receipts:
        evidence.append('explicit-openrouter-receipt-source')
    origin = row.get('upstream_origin')
    conflicts = []
    # direct_meta was hard-coded by Ledger.reserve even with a configurable
    # gateway upstream. Retain disagreement without promoting that label to fact.
    if origin and evidence and origin != 'openrouter':
        conflicts.append('recorded-origin-disagrees-with-openrouter-evidence')
    generation = row.get('generation_receipt') or {}
    matched = (len(identities) == 1 and generation.get('id') in identities and
               row.get('generation_receipt_source') == 'openrouter.generation.total_cost')
    provider = row.get('served_provider')
    generation_provider = generation.get('provider_name') if matched else None
    if provider and generation_provider and provider != generation_provider:
        conflicts.append('response-and-generation-providers-disagree')
    receipt_ids = {receipt['generation_id'] for receipt in receipts}
    if len(identities | receipt_ids) > 1:
        conflicts.append('response-generation-identities-disagree')
    if (generation and row.get('generation_receipt_source') == 'openrouter.generation.total_cost'
            and not matched):
        conflicts.append('generation-receipt-identifies-different-request')
    if row.get('_duplicate_generation_id'):
        conflicts.append('generation-identity-shared-by-multiple-requests')
    status = ('conflicting' if conflicts else 'proven' if evidence else
              'recorded-only' if origin or row.get('actual_cost_source') or generation else 'missing')
    return {'route_status': status, 'route_evidence': evidence, 'route_conflicts': conflicts,
            'recorded_origin': origin if origin in {'direct_meta', 'openrouter'} else 'other' if origin else None,
            'receipt_billing_provider': 'OpenRouter' if receipts else None,
            'billing_provider': 'OpenRouter' if status == 'proven' else None,
            'upstream_provider': (provider or generation_provider) if status == 'proven' else None,
            'recorded_upstream_provider': provider,
            'generation_route_bound': matched}


def request_record(row):
    receipts, identities = receipt_evidence(row)
    costs = {amount(r['usd']) for r in receipts}
    receipt_ids = {r['generation_id'] for r in receipts}
    conflict = len(costs) > 1 or len(identities | receipt_ids) > 1 or bool(row.get('_duplicate_generation_id'))
    actual = next(iter(costs)) if len(costs) == 1 and not conflict else None
    bound = amount(row['accounted']) if isinstance(row.get('accounted'), str) else amount(row.get('accounted_upper_bound_usd'))
    duration = None
    try:
        if row.get('started') and row.get('finished'):
            seconds = (datetime.fromisoformat(row['finished']) - datetime.fromisoformat(row['started'])).total_seconds()
            if seconds >= 0:
                duration = str(seconds)
    except (ValueError, TypeError):
        pass
    generation_receipt = row.get('generation_receipt') or {}
    route = route_evidence(row, receipts, identities)
    lookup_matches = route['generation_route_bound']
    record = {k: row.get(k) for k in ('id', 'project', 'model', 'started', 'finished',
              'state', 'http_status', 'error_label', 'explicit_retry_of', 'accounting_role')}
    record.update(generation_ids=sorted(identities | receipt_ids), cost_receipts=receipts,
        actual_usd=usd(actual), actual_display_usd=cents(actual), receipt_conflict=conflict,
        conservative_accounted_usd=usd(bound), duration_s=duration,
        token_price_estimate_usd=usd(amount(row.get('token_price_estimate_usd'))),
        generation_lookup_identity_mismatch=bool(generation_receipt) and
            (len(identities) != 1 or generation_receipt.get('id') not in identities),
        generation_lookup_route_unverified=bool(generation_receipt) and
            row.get('generation_receipt_source') != 'openrouter.generation.total_cost',
        requested_endpoint=row.get('endpoint'),
        retry_lineage_available=('explicit_retry_of' in row or 'attempt_number' in (row.get('dispatch_context') or {})),
        dispatch_context={k: v for k, v in (row.get('dispatch_context') or {}).items()
                          if k in {'run_id','arm_id','phase','operation_id','attempt_id','attempt_number','retry_of','worker_id','process_instance_id'}
                          and (type(v) is int or isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', v))}, receipt_metadata_incomplete=row.get('receipt_metadata_incomplete', False))
    record.update(route)
    def mapping(value):
        if isinstance(value, str):
            try: value = json.loads(value)
            except (ValueError, TypeError): return {}
        return value if isinstance(value, dict) else {}
    detail = mapping(row.get('usage_detail'))
    generation = generation_receipt if lookup_matches else mapping(row.get('generation_usage_detail'))
    for key, names in {'input_tokens': ('native_tokens_prompt',), 'output_tokens': ('native_tokens_completion',),
                       'reasoning_tokens': ('native_tokens_reasoning', 'completion_tokens_details.reasoning_tokens', 'output_tokens_details.reasoning_tokens', 'reasoning_tokens'),
                       'cached_tokens': ('native_tokens_cached', 'prompt_tokens_details.cached_tokens', 'input_tokens_details.cached_tokens', 'cache_read_tokens', 'cached_tokens')}.items():
        candidates = [row.get(key), *(generation.get(n) for n in names), *(detail.get(n) for n in names)]
        record[key] = next((v for v in candidates if type(v) is int and v >= 0), None)
    return record


def summarize(rows):
    records = [request_record(row) for row in rows]
    owners = {}
    for record in records:
        for identifier in record['generation_ids']:
            owners.setdefault(identifier, set()).add(record['id'])
    duplicates = {identifier for identifier, ids in owners.items() if len(ids) > 1}
    for record in records:
        if duplicates.intersection(record['generation_ids']):
            record.update(actual_usd=None, actual_display_usd=None, receipt_conflict=True,
                          route_status='conflicting', billing_provider=None, upstream_provider=None)
    known = [amount(r['actual_usd']) for r in records if r['actual_usd'] is not None]
    complete = bool(records) and len(known) == len(records)
    observed = sum(known, Decimal(0)) if known else None
    bounds = [amount(r['conservative_accounted_usd']) for r in records]
    estimates = [amount(r['token_price_estimate_usd']) for r in records
                 if r['actual_usd'] is None and r['token_price_estimate_usd'] is not None]
    unresolved = [amount(r['conservative_accounted_usd']) for r in records if r['state'] != 'settled']
    def available(values):
        return usd(sum(values, Decimal(0))) if values and all(v is not None for v in values) else None
    result = {'requests': len(records), 'actual_receipt_requests': len(known),
        'actual_unknown_requests': len(records) - len(known), 'actual_coverage_complete': complete,
        'actual_reported_usd': usd(observed), 'actual_display_usd': cents(observed),
        'actual_total_usd': usd(observed) if complete else None,
        'receipt_conflicts': sum(r['receipt_conflict'] for r in records),
        'receipt_metadata_incomplete_requests': sum(r['receipt_metadata_incomplete'] for r in records),
        'settled_requests': sum(r['state'] == 'settled' for r in records),
        'token_price_estimate_usd': available(estimates),
        'accounted_without_actual_usd': available([amount(r['conservative_accounted_usd']) for r in records if r['actual_usd'] is None]),
        'conservative_accounted_usd': available(bounds),
        'unresolved_requests': sum(r['state'] != 'settled' for r in records),
        'unresolved_bound_usd': available(unresolved),
        'requests_missing_accounted_bound': sum(b is None for b in bounds),
        'retry_requests': sum(bool(r['explicit_retry_of']) or r['dispatch_context'].get('attempt_number', 1) > 1 for r in records),
        'requests_missing_retry_lineage': sum(not r['retry_lineage_available'] for r in records),
        'failed_http_requests': sum(type(r['http_status']) is int and r['http_status'] >= 400 for r in records),
        'requests_missing_http_status': sum(r['http_status'] is None for r in records),
        'timed_requests': sum(r['duration_s'] is not None for r in records),
        'observed_request_duration_s': available([amount(r['duration_s']) for r in records if r['duration_s'] is not None]),
        'request_ids': [r['id'] for r in records]}
    for key in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cached_tokens'):
        values = [r[key] for r in records if r[key] is not None]
        result['observed_' + key] = sum(values) if values else None
        result['requests_missing_' + key] = len(records) - len(values)
    result['requests_missing_token_counts'] = sum(r['input_tokens'] is None or r['output_tokens'] is None for r in records)
    # Compatibility aliases; observed detail subsets must never be added to input/output totals.
    for name in ('reasoning', 'cached'):
        result['observed_generation_' + name + '_tokens'] = result['observed_' + name + '_tokens']
        result['requests_missing_generation_' + name + '_counts'] = result['requests_missing_' + name + '_tokens']
    return result


RESPONSE_PROTOCOL = 'openrouter-response-billing-v1'


def loads(text):
    return json.loads(text, parse_float=Decimal)


def sanitized(event):
    out={k:event[k][:256] for k in ('id','model','provider') if isinstance(event.get(k),str)}
    choices=event.get('choices') or []
    reasons=[c['finish_reason'][:64] for c in (choices if isinstance(choices,list) else [])
             if isinstance(c,dict) and isinstance(c.get('finish_reason'),str)]
    if reasons:out['finish_reasons']=reasons
    usage=event.get('usage')
    if isinstance(usage,dict):
        clean={}
        for key,value in usage.items():
            if key=='cost':
                if not isinstance(value,bool) and isinstance(value,(int,float,Decimal,str)):
                    try:
                        cost=Decimal(str(value))
                        if cost.is_finite() and cost>=0:clean[key]=str(cost)
                    except Exception:pass
            elif key in {'prompt_tokens','completion_tokens','total_tokens'}:
                if type(value) is int and value>=0:clean[key]=value
            elif key in {'prompt_tokens_details','completion_tokens_details','input_tokens_details','output_tokens_details','cost_details'} and isinstance(value,dict):
                detail={}
                for name,number in value.items():
                    if (key=='cost_details' and name in {'upstream_inference_cost','upstream_inference_prompt_cost','upstream_inference_completions_cost'} and
                            not isinstance(number,bool) and isinstance(number,(int,float,Decimal))):
                        n=Decimal(str(number))
                        if n.is_finite() and n>=0:detail[name]=str(n)
                    elif name in {'cached_tokens','cache_write_tokens','reasoning_tokens','audio_tokens','video_tokens','image_tokens'} and type(number) is int and number>=0:
                        detail[name]=number
                if detail:clean[key]=detail
        if clean:out['usage']=clean
    return out


class EventReader:
    """Incremental SSE frames; an invalid tail cannot erase preceding frames."""
    def __init__(self, callback=None):
        self.callback=callback;self.decoder=codecs.getincrementaldecoder('utf-8')()
        self.buffer='';self.lines=[];self.events=[];self.errors=0;self.sse=False
        self.done_frames=0;self.data_after_done=False

    @property
    def sse_done_observed(self):
        return self.done_frames == 1 and not self.data_after_done

    def _event(self, *, framed=False):
        if not self.lines:return
        text='\n'.join(self.lines);self.lines=[]
        if text.strip()=='[DONE]':
            # HTTP EOF can flush a partial SSE frame. Only a frame with its
            # actual blank-line delimiter proves a terminal marker on the wire.
            self.done_frames += int(framed and text == '[DONE]')
            return
        if not text.strip():return
        try:
            event=loads(text)
            if not isinstance(event,dict):raise ValueError('Not an object')
        except (ValueError,TypeError):self.errors+=1;return
        self.events.append(event)
        if self.callback:self.callback(event)

    def feed(self,data):
        try:self.buffer+=self.decoder.decode(data)
        except UnicodeError:self.errors+=1;return
        while '\n' in self.buffer:
            line,self.buffer=self.buffer.split('\n',1);line=line.rstrip('\r')
            if line.startswith('data:'):
                if self.done_frames:self.data_after_done=True
                self.sse=True;self.lines.append(line[5:].lstrip(' '))
            elif not line and self.sse:self._event(framed=True)
            elif not line:continue
            elif line.startswith((':','event:','id:','retry:')):self.sse=True
            elif not self.sse:
                # Non-streaming JSON can contain newlines. Keep it intact.
                self.buffer=line+'\n'+self.buffer;break

    def finish(self):
        try:self.buffer+=self.decoder.decode(b'',final=True)
        except UnicodeError:self.errors+=1
        if self.sse or self.buffer.startswith('data:'):
            if self.buffer.startswith('data:'):
                if self.done_frames:self.data_after_done=True
                self.lines.append(self.buffer[5:].strip())
            elif self.buffer.strip() and not self.buffer.startswith(':'):self.errors+=1
            self.buffer='';self._event()
        elif self.buffer.strip():
            self.lines=[self.buffer];self.buffer='';self._event()
        return self.events


def read_frames(data):
    reader=EventReader();reader.feed(data);reader.finish();return reader


def parse_response(data):
    reader=read_frames(data);return reader.events,reader.errors


def receipt(identifier,event,*,status,body_sha256,complete):
    safe=sanitized(event)
    return {'protocol':RESPONSE_PROTOCOL,'attempt_id':identifier,'http_status':status,
        'generation_id':safe.get('id'),'model':safe.get('model'),'provider':safe.get('provider'),
        'usage':safe.get('usage',{}),'finish_reasons':safe.get('finish_reasons',[]),
        'body_sha256':body_sha256,'body_complete':bool(complete),
        'cost_source':'response.usage.cost','reported_zero':
            Decimal(safe['usage']['cost'])==0 if 'cost' in safe.get('usage',{}) else False}


def financials(row):
    """Legacy rows remain readable; their exposure is never called a charge."""
    state=row['state'];known=state=='settled'
    amount=str(row.get('settled_charge_usd',row['accounted'])) if known else None
    return {'settled_charge_usd':amount,'outstanding_reservation_usd':row['accounted'] if state=='reserved' else '0',
        'unresolved_charge_ceiling_usd':row['accounted'] if state not in {'settled','reserved','allocated','rejected_no_dispatch'} else '0',
        'external_allocation_usd':row['accounted'] if state=='allocated' else '0',
        'exposure_usd':row['accounted'],'reservation_ceiling_usd':row.get('reserved'),
        'charge_evidence':'sanitized-response-receipt' if row.get('billing_receipt') else
            'authoritative-generation-reconciliation' if (row.get('reconciliation') or {}).get('cost_source')=='total_cost' else
            'legacy-ledger-reported-charge-without-raw-receipt' if known else 'unknown'}


def read_attempts(path):
    """Replay the authoritative journal under a shared lock, including torn-tail checks."""
    import fcntl

    from .receipt_projection import Projection

    path = Path(path)
    projection = Projection(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if os.path.lexists(projection.index):
            raise
        return []
    with os.fdopen(fd, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        projection.require_complete()
        return list(replay_attempts(stream).values())


VERSION = 'direct-client-reservations-v1'


def money(value):
    if isinstance(value, bool):
        raise ValueError('Invalid money')
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError('Invalid money') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid money')
    return result


def observed_generation_id(row):
    """Use an actual response ID, never manufacture one from timestamps."""
    body=row.get('upstream_request_id')
    header=row.get('response_diagnostic_headers',{}).get('x-generation-id')
    if body and header and body!=header:
        raise ValueError('Response generation identifiers disagree')
    if body:return body,'response-body:id'
    if isinstance(header,str) and re.fullmatch(r'gen-[A-Za-z0-9_-]+',header):
        return header,'response-header:x-generation-id'
    raise ValueError('No observed generation identifier')


def replay_attempts(stream):
 """Reconstruct each run-owned attempt from the append-only receipt journal."""
 rows={}
 for line in stream:
  if not line.strip():continue
  event=json.loads(line)
  ident=event['id']
  if event['kind']=='prepared':
   if ident in rows or event['row']['id']!=ident:raise ValueError('Duplicate or foreign prepared receipt')
   rows[ident]=event['row']
  else:rows[ident].update(event['patch'])
 return rows


ORIGIN='https://openrouter.ai/api/v1/'


def read_cancelled_attempt(path):
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


def liability_envelope(calls, limit, cohort_id, *, cap='20'):
    """A resumed child needs funding for the remaining envelope, not twice its spent part."""
    from decimal import Decimal
    total=sum((Decimal(str(r['accounted'])) for r in calls),Decimal(0))
    own=sum((Decimal(str(r['accounted'])) for r in calls if r.get('cohort_id')==cohort_id),Decimal(0))
    available=Decimal(cap)-total
    remaining=max(Decimal(0),Decimal(limit)-own)
    return {'available_usd':str(available),'cohort_accounted_usd':str(own),
            'remaining_envelope_usd':str(remaining),'additional_headroom_usd':str(max(Decimal(0),remaining-available)),
            'funded':remaining<=available}
