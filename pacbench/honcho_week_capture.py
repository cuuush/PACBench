"""Bind stock post-turn appends and their paid worker jobs to the Hermes transcript."""
from hashlib import sha256
import json
from pathlib import Path
import time

from . import honcho_daily_gate as gate, honcho_week_state as week
from .canary_capability import binding, read
from .dispatch_ownership import OwnershipConflict
from .workflow_spec import instant


def prepare(scope,current,receipt,prompt,answer,session_id):
    from .native_writes import capture_description

    index,turn,_ = week.pending(scope)
    if (prompt!=turn.prompt or session_id!=current['session_id'] or not isinstance(answer,str) or not answer.strip()
            or receipt['response_validation']['assistant_trimmed_text_sha256']!=sha256(answer.strip().encode()).hexdigest()):
        raise OwnershipConflict('Stock Honcho capture differs from the active user and settled assistant output')
    capture = capture_description({'provider':'honcho','captures':[dict(session=session_id,prompt=prompt,answer=answer)]})
    native = gate.runtime(Path(scope['root']))['host']
    if (capture['workspace'],capture['owner'],capture['assistant']) != (native['workspace'],native['peerName'],native['aiPeer']):
        raise OwnershipConflict('Stock Honcho capture is outside the owned workspace or peers')
    desc, = capture['captures']
    pieces = [dict(peer=chunk['peer'],sha256=sha256(chunk['content'].encode()).hexdigest()) for chunk in desc['chunks']]
    if set(part['peer'] for part in pieces)!={native['peerName'],native['aiPeer']}:
        raise OwnershipConflict('Expected native capture for both sides of the completed question')
    root=Path(scope['root'])
    return gate._write(root/'chat-writes'/f'{index+1:02d}.json',dict(index=index+1,question=turn.id,
        session=week.capture_session_name(desc['session']),session_id=session_id,answer_receipt=receipt['id'],
        submitted=current,turn_sha256=desc['binding'],chunks=pieces))


def intents(scope):
    root=Path(scope['root'])
    paths=sorted((root/'chat-writes').glob('*.json'))
    values=[read(binding(path)) for path in paths]
    if (paths != [root/'chat-writes'/f'{i+1:02d}.json' for i in range(len(values))]
            or len(values)>len(week.plan()[1]) or len({r['session'] for r in values})>1
            or len({r['session_id'] for r in values})>1
            or any(r['index']!=i+1 or r['question']!=week.plan()[1][i].id for i,r in enumerate(values))):
        raise OwnershipConflict('Honcho native chat writes are out of order or cross sessions')
    return values


def snapshot(scope):
    """Read persisted message hashes and actual stock queue ownership in one statement."""
    from provider_adapters.honcho_checkpoint import sql

    root=Path(scope['root'])
    native=gate.runtime(root)
    cid=gate.owned_container(root,native)
    space, source=native['host']['workspace'],week.capture_session_name(scope['session'])
    return json.loads(sql(cid,'postgres',f"""
        SELECT json_build_object(
          'messages', (SELECT coalesce(json_agg(json_build_object(
             'public_id',m.public_id,'local_id',m.id,'session',m.session_name,'session_id',s.id,
             'peer',m.peer_name,'at',m.created_at,'seq',m.seq_in_session,
             'sha256',encode(sha256(convert_to(m.content,'UTF8')),'hex'),
             'meta',m.metadata,'file',m.internal_metadata,
             'embeddings',(SELECT json_build_array(count(*),
                count(*) FILTER (WHERE e.sync_state='synced' AND e.embedding IS NOT NULL),
                count(*) FILTER (WHERE e.sync_state='failed')) FROM message_embeddings e
                WHERE e.tenant_id=m.tenant_id AND e.message_id=m.public_id
                  AND e.workspace_name=m.workspace_name)) ORDER BY m.seq_in_session,m.id),'[]'::json)
            FROM messages m JOIN sessions s ON s.workspace_name=m.workspace_name AND s.name=m.session_name
             AND s.tenant_id=m.tenant_id WHERE m.workspace_name='{space}' AND m.session_name<>'{source}'
             AND m.deleted_at IS NULL AND s.deleted_at IS NULL AND s.is_active),
          'queue',(SELECT coalesce(json_agg(json_build_object(
            'id',q.id,'type',q.task_type,'message_id',q.message_id,'unit',q.work_unit_key,
            'session_id',q.session_id,'tenant',q.tenant_id,'observed',q.payload->>'observed',
            'observers',q.payload->'observers','session',q.payload->>'session_name',
            'at',q.payload->>'created_at','sha256',encode(sha256(convert_to(q.payload->>'content','UTF8')),'hex'),
            'processed',q.processed,'failed',q.error IS NOT NULL,'claim',a.id) ORDER BY q.id),'[]'::json)
            FROM queue q LEFT JOIN active_queue_sessions a ON a.work_unit_key=q.work_unit_key
             AND a.tenant_id IS NOT DISTINCT FROM q.tenant_id WHERE q.workspace_name='{space}'),
          'units',(SELECT coalesce(json_agg(json_build_object(
            'unit',b.work_unit_key,'type',b.task_type,'eligible', b.task_type<>'representation'
               OR b.total_tokens>=settings.target OR b.oldest_created_at<=now()-make_interval(secs=>settings.max_age))),'[]'::json)
            FROM queue_item_batches b CROSS JOIN (SELECT 512 AS target,1800 AS max_age) settings
             WHERE split_part(b.work_unit_key,':',2)='{space}'))
        """))


def inventory(scope,state):
    saved=intents(scope)
    expected=[dict(piece,origin=i['question'],native_session=i['session']) for i in saved for piece in i['chunks']]
    rows=state['messages']
    for seq,(actual,planned) in enumerate(zip(rows,expected),1):
        if ((actual['seq'],actual['session'],actual['peer'],actual['sha256']) !=
                (seq,planned['native_session'],planned['peer'],planned['sha256'])
                or actual['file'].get('file_id') or actual['meta'] not in ({},None)):
            raise OwnershipConflict('Honcho backend changed the exact captured chat prefix')
    if (len(rows)>len(expected) or any(len({row[key] for row in rows})!=len(rows) for key in ('public_id','local_id'))
            or len({row['session_id'] for row in rows})>1):
        raise OwnershipConflict('Honcho backend has extra, duplicate or foreign chat rows')
    return {m['local_id']:{**m,'origin':w['origin']} for m,w in zip(rows,expected)},len(expected)


def native_batch(scope,native,telemetry,batch,unit,claim):
    """Resolve a durable chat-triggered representation or summary to actual stock inputs."""
    state = snapshot(scope)
    members,_ = inventory(scope,state)
    qids = telemetry.queue_item_ids or []
    tasks = [q for q in state['queue'] if q['id'] in qids]
    host = native['host']
    if (not tasks or [q['id'] for q in tasks]!=qids or len(qids)!=len(set(qids))
            or telemetry.workspace_name!=host['workspace']
            or any(q['unit']!=unit or q['claim']!=claim or q['tenant'] is not None or q['failed'] or q['processed']
                   or q['session_id']!=telemetry.session_id or q['message_id'] not in members for q in tasks)):
        raise OwnershipConflict('Honcho chat derivation differs from the owner, queue and captured inputs')
    trigger=[members[q['message_id']] for q in tasks]
    purpose=telemetry.call_purpose
    expected_type='representation' if purpose=='deriver.representation' else 'summary'
    if any(q['type']!=expected_type for q in tasks):
        raise OwnershipConflict('Honcho chat job purpose does not match its stock queue')
    if expected_type=='representation':
        if not batch:
            raise OwnershipConflict('Honcho capture representation lacks its native input batch')
        messages,ctx=batch[0]
        if (ctx.get('queue_item_ids')!=qids or ctx.get('queue_item_message_ids')!=[r['local_id'] for r in trigger]
                or ctx.get('session_id')!=telemetry.session_id or ctx.get('observed')!=telemetry.observed
                or sorted(ctx.get('observers') or ())!=sorted(telemetry.observers or ())
                or telemetry.source_message_ids!=[r['public_id'] for r in trigger]
                or any(q['observed']!=r['peer'] or q['observed']!=telemetry.observed
                       or q['session']!=r['session'] or q['sha256']!=r['sha256'] or instant(q['at'])!=instant(r['at'])
                       or sorted(q['observers'])!=sorted(telemetry.observers) for q,r in zip(tasks,trigger))):
            raise OwnershipConflict('Honcho captured message and representation batch identities differ')
        observed={m.id:(m.public_id,m.session_name,m.peer_name,instant(m.created_at.isoformat()),
                        sha256(m.content.encode()).hexdigest()) for m in messages if m.workspace_name==host['workspace']}
        expected={key:(row['public_id'],row['session'],row['peer'],instant(row['at']),row['sha256'])
                  for key,row in members.items() if key in observed}
        if len(messages)!=len(observed) or observed!=expected or not {r['local_id'] for r in trigger}<=set(observed):
            raise OwnershipConflict('Honcho chat representation contains an unknown model input')
    else:
        source = list(telemetry.source_message_ids or ())
        limit = 20 if purpose=='summary.short' else 60
        allowed = [r['public_id'] for r in members.values()
                   if trigger[0]['seq']-limit < r['seq'] <= trigger[0]['seq']]
        if (len(tasks)!=1 or trigger[0]['seq']%limit or not source or source!=allowed[-len(source):]
                or telemetry.custom_instructions):
            raise OwnershipConflict('Honcho summary exceeds its exact bounded native chat window')
    return dict(provider='honcho',kind='background',honcho_chat_work=dict(
        purpose=purpose,queue_ids=qids,native_message_ids=telemetry.source_message_ids,
        questions=[r['origin'] for r in trigger],session_id=telemetry.session_id)),dict(
        queue_ids=qids,claim=claim,trace=telemetry.trace_id)


def barrier(scope,owner,ledger,*,captures):
    """Fence open-chat writes and eligible work while preserving stock age-gated work."""
    from .honcho_first_answer import check_stops
    from .gateway_reuse import PROTOCOL

    root=Path(scope['root'])
    start=time.monotonic_ns()
    deadline=time.monotonic()+90
    witness=owner.witness
    observer=owner.home/'reuse-observer.json'
    while time.monotonic()<deadline:
        check_stops(root.parent.parent)
        if (owner.home/'reuse-observer-error.json').exists():
            raise OwnershipConflict('Honcho open-chat observer failed')
        sample=json.loads(observer.read_bytes())
        if ((sample['pid'],sample['start_time'],sample['protocol'],sample['observer_sha256'])!=
                (witness['pid'],witness['start_time'],PROTOCOL,owner.pins['reuse_observer_sha256'])
                or sample['native_failures']):
            raise OwnershipConflict('Honcho open-chat observer identity or health differs')
        providers=[p for s in sample['sessions'] for p in s['providers'] if p['name']=='honcho']
        if (sample['observed_monotonic_ns'] < start or sample['native_release_threads']
                or any(not name.startswith('mem-sync_') for name in sample['native_memory_threads'])
                or any(s['pending_futures'] for s in sample['sessions'])
                or any(any(name!='honcho-async-writer' for name in p.get('workers',()))
                       or p.get('pending')!=0 or p.get('writer_queue')!=0 or p.get('active_flushes')!=0
                       for p in providers)
                or any(not row.get('finished') for row in ledger.snapshot())):
            time.sleep(.15)
            continue
        current=snapshot(scope)
        rows,expected=inventory(scope,current)
        saved=intents(scope)
        tasks=[q for q in current['queue'] if q['message_id'] in rows]
        if any(q['failed'] for q in current['queue']) or any(m['embeddings'][2] for m in rows.values()):
            raise OwnershipConflict('Honcho recorded a native indexing or worker failure')
        pending=[q for q in tasks if not q['processed']]
        due={u['unit'] for u in current['units'] if u['eligible']}
        if (len(saved)!=captures or len(rows)!=expected or any(q['claim'] or q['unit'] in due for q in pending)
                or set(rows)!={q['message_id'] for q in tasks if q['type']=='representation'}
                or any(not m['embeddings'][0] or m['embeddings'][0]!=m['embeddings'][1] for m in rows.values())):
            time.sleep(.25)
            continue
        return dict(gateway_identity=[witness['pid'],witness['start_time']],pending_native_writes=0,
                    inflight_requests=0,native_errors=[],readback=dict(captures=captures,messages=list(rows.values()),
                    deferred_queue_ids=[q['id'] for q in pending],gateway_observer_monotonic_ns=sample['observed_monotonic_ns']))
    raise TimeoutError('Stock Honcho chat capture or eligible native work did not reach the open-session fence')
