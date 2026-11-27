"""Stock Honcho document and processing observations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime


def audit_honcho_receipt(batch, result, previous_work=None, previous_rosters=None):
    """Independently bind native acknowledgments and readiness to frozen sources."""
    if previous_work is None:previous_work={}
    errors=[];seen=[];sessions=set();minimum={}
    if result.get('membership_protocol') is not None:
        from provider_adapters.honcho_enrollment import (
            SUPPORTED,
            source_plans,
            validate_roster_continuity,
            validate_windows,
        )
        try:
            if result['membership_protocol'] not in SUPPORTED:
                raise ValueError('Unsupported Honcho membership protocol')
            plans = source_plans(batch, {'peerName':'user','aiPeer':'hermes'},protocol=result['membership_protocol'])
            validate_windows(plans, result['membership_witness'])
            if previous_rosters is not None:
                previous_rosters.update(validate_roster_continuity(plans, previous_rosters))
        except (ValueError,KeyError,TypeError):
            errors.append('Honcho chronological source membership is unproven')
    expected={m['source_id']:m for m in batch}
    for ack in result.get('result',[]):
        sessions.add(ack['session_id'])
        baseline=ack.get('processing_baseline',{})
        prior=max(previous_work.get(ack['session_id'],0),baseline.get('total_work_units',0))
        minimum[ack['session_id']]=prior+len(ack['messages'])
        if 'minimum_work_units' in ack and ack['minimum_work_units']!=minimum[ack['session_id']]:
            errors.append('Honcho new-work enrollment watermark differs from acknowledged sources')
        observer=ack.get('observer',{})
        if observer.get('peer_id')!='hermes' or observer.get('observe_others') is not True:
            errors.append('Honcho configured recall observer did not witness sources')
        for actual in ack['messages']:
            metadata=actual.get('metadata',{});source_id=metadata.get('source_id')
            seen.append(source_id);original=expected.get(source_id)
            if original is None:
                errors.append('Honcho acknowledged an unknown source');continue
            identity=(original['channel'],original['thread'])
            session='source-'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            if actual.get('session_id')!=session or ack['session_id']!=session:
                errors.append('Honcho acknowledged the wrong source session')
            from provider_adapters.honcho_enrollment import (
                LEGACY_PROTOCOL,
                SUPPORTED,
                source_peer_id,
            )
            peer_protocol=result.get('membership_protocol') or LEGACY_PROTOCOL
            if peer_protocol not in SUPPORTED:peer_protocol=LEGACY_PROTOCOL
            peer=source_peer_id('Me' if original['from_owner'] else original['sender'],
                {'peerName':'user','aiPeer':'hermes'},protocol=peer_protocol)
            if (actual.get('peer_id')!=peer or
                    actual.get('content')!=original['text'] or
                    datetime.fromisoformat(actual['created_at'])!=datetime.fromisoformat(original['written_at']) or
                    any(metadata.get(k)!=v for k,v in original.items() if k!='text')):
                errors.append('Honcho acknowledged changed source text, speaker, time or metadata')
    if sorted(seen)!=sorted(expected):errors.append('Honcho acknowledgments omit or duplicate sources')
    changes=result.get('processing_status',[])
    error_scope=result.get('source_error_scope')
    if error_scope not in (None,'submitted-source-sessions-v1'):
        errors.append('Unsupported Honcho source error scope')
    from provider_adapters.honcho_clock import SUPPORTED as HONCHO_CLOCK_PROTOCOLS
    if result.get('clock_protocol') in HONCHO_CLOCK_PROTOCOLS:
        ids = {m['id'] for ack in result.get('result', []) for m in ack['messages']}
        witnesses = result.get('message_witnesses', [])
        final = changes[-1] if changes else {}
        def source_errors(state):
            failures=state.get('errors',[])
            if error_scope=='submitted-source-sessions-v1':
                return [e for e in failures if e.get('session_name') is None or e['session_name'] in sessions]
            return failures
        covered={r.get('id') for r in witnesses if r.get('processed') is True and r.get('has_error') is False}
        cleanup=result.get('queue_cleanup_evidence')
        if cleanup is not None:
            from provider_adapters.honcho_clock import cleaned_queue_coverage
            try:
                if cleanup['protocol']!='honcho-enrolled-queue-success-cleanup-v1':raise ValueError('Unsupported cleanup proof')
                lower_bound=min((m['created_at'] for a in result['result'] for m in a['messages']),key=datetime.fromisoformat)
                cleaned=cleaned_queue_coverage(witnesses,changes,retention_seconds=cleanup['retention_seconds'],not_before=lower_bound)
                if sorted(cleaned)!=cleanup['message_ids']:raise ValueError('Cleanup coverage differs')
                covered.update(cleaned)
            except (ValueError,KeyError,TypeError):errors.append('Honcho successful native queue cleanup is unproven')
        proof=result.get('completion_evidence')
        if proof is not None:
            from provider_adapters.honcho_clock import citation_coverage
            try:
                if proof.get('protocol')!='honcho-native-conclusion-source-v1':raise ValueError('Unsupported completion evidence')
                covered.update(citation_coverage(proof.get('citations',[]),result.get('result',[])))
            except (ValueError,KeyError,TypeError):errors.append('Honcho persistent completion citations are invalid')
        if (not ids or covered != ids or not {r.get('id') for r in witnesses}<=ids or
                len({r.get('queue_id') for r in witnesses}) != len(witnesses) or
                any(r.get('has_error') is not False for r in witnesses) or
                any(final.get(k) != 0 for k in ('claims','pending','embeddings')) or
                any(source_errors(s) for s in changes)):
            errors.append('Honcho simulated-clock message enrollment/completion is unproven')
        return errors
    if any(change.get('native_queue_errors') for change in changes):
        errors.append('Honcho submitted-source work completed with native errors')
    if any(not e.get('session_name') or e['session_name'] in sessions
           for change in changes for e in change.get('unrelated_queue_errors',[])):
        errors.append('Honcho excluded native error lacks unrelated-session attribution')
    final=changes[-1].get('sessions',{}) if changes else {}
    if set(final)!=sessions or not final or any(
            status.get('pending_work_units')!=0 or status.get('in_progress_work_units')!=0
            for status in final.values()):
        errors.append('Honcho submitted-session processing did not complete')
    for id in sessions:
        if final.get(id,{}).get('total_work_units',0)<minimum[id]:
            errors.append('Honcho readiness preceded new source work enrollment')
        previous_work[id]=max(minimum[id],final.get(id,{}).get('total_work_units',0))
    return errors
