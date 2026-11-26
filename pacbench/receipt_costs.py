"""Offline, Decimal-only receipt accounting. Accounting state is not billing proof."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import hashlib
import json
import os
from pathlib import Path
import tempfile

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
