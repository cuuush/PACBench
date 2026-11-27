"""Verify the actual driver inputs, tool rounds and saved native turn.  Read-only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pacbench import harness
from pacbench.context_isolation import judge_question
from pacbench.gateway_reuse import tree_manifest
from pacbench.hermes_gateway import GatewayLifecycleError, _live
from pacbench.judging.grade import LEGACY_DIAGNOSTIC_VERSION
from pacbench.judging.recovery import tool_profile
from pacbench.providers.memcal.source import verify_captured_driver

from ..provider_versions import MEMCAL_WEEK_VERSION
from .recording import retry_activity, tool_activity, tool_text_boundary
from .spec import instant


def inspect_source_cutoff(surface, documents, prefix, public_prompts):
    """Catch verbatim future source bodies in actual inputs; inventories remain the primary gate."""
    ready = set(prefix)
    allowed = '\n'.join([d.text for d in documents if d.filename in ready] + public_prompts)
    # Exclude short/common phrases and already public quotations. Scan decoded
    # contents, including automatic recall and serialized multi-line tool results.
    observed = []
    for request in surface['model_requests']:
        for row in request['messages']:
            if row['role'] in ('system', 'user', 'tool'):
                content = row.get('content')
                if isinstance(content, str):
                    observed.append(content)
    if surface['prefetch']:
        observed.append(surface['prefetch'])
    for doc in documents:
        if doc.filename in ready:
            continue
        for body in doc.bodies:
            if len(body) >= 40 and body not in allowed:
                escaped = json.dumps(body, ensure_ascii=False)[1:-1]
                if any(body in item or escaped in item for item in observed):
                    raise ValueError('answer input contains a verbatim unavailable source body')


def _tool_call_contract(call, *, stored=False):
    # Hermes persists Responses metadata next to the Chat Completions fields.
    # Only its local metadata may be absent from the request sent to the model.
    contract = dict(call)
    if stored:
        if contract.pop('call_id', contract['id']) != contract['id']:
            raise ValueError('native tool call ID aliases disagree')
        contract.pop('response_item_id', None)
    function = dict(contract['function'])
    arguments = json.loads(function['arguments'])
    function['arguments'] = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return {**contract, 'function':function}


def _wire_tool_text(saved, sent):
    return (isinstance(saved, str) and isinstance(sent, str)
            and saved.rstrip('\r\n') == sent and saved != sent and bool(sent))


def _saved_calls(message):
    calls = message.get('tool_calls') or []
    return json.loads(calls) if isinstance(calls, str) else calls


def _tool_rounds(history, last_user):
    """Find the requests that could have produced this completed native turn."""
    rounds, calls, outstanding = [], [], set()
    for index, row in enumerate(history[last_user + 1:-1], last_user + 1):
        if row['role'] == 'assistant':
            group = _saved_calls(row)
            if outstanding or not group:
                raise ValueError('native tool group is incomplete or out of order')
            rounds.append(index)
            for call in group:
                fn = call['function']
                calls.append((call['id'], fn['name'], json.loads(fn['arguments'])))
                outstanding.add(call['id'])
        else:
            call_id = row.get('tool_call_id')
            if call_id not in outstanding:
                raise ValueError('native tool group contains a result from another round')
            outstanding.remove(call_id)
    if outstanding or _saved_calls(history[-1]):
        raise ValueError('native tool group did not finish before the final answer')
    rounds.append(len(history) - 1)
    return rounds, calls


def captured_turn(surface, turn, prior, condition, expected_prefix):
    history = surface['history']
    previous = prior['history'] if prior else []
    barrier = surface['write_barrier']
    if (not isinstance(barrier, dict) or barrier.get('session_id') != surface['session_id']
            or barrier.get('pending_writes') != 0 or not barrier.get('readback')):
        raise ValueError('native turn writes are unverified')
    if (not surface['session_id'] or not surface['gateway_identity']
            or instant(surface['question_at']) != instant(turn.at)
            or tuple(sorted(surface['offered_tools'])) != tuple(sorted(condition.tool_names))):
        raise ValueError('missing native session, clock, write barrier or tool surface')
    if prior and ((surface['session_id'], surface['gateway_identity']) !=
                  (prior['session_id'], prior['gateway_identity'])):
        raise ValueError('conversation changed session or restarted its gateway')
    if history[:len(previous)] != previous:
        raise ValueError('native conversation prefix changed')
    delta = history[len(previous):]
    if (len(delta) < 2 or delta[0]['role'] != 'user' or delta[0]['content'] != turn.prompt
            or any(r['role'] not in ('assistant', 'tool') for r in delta[1:])
            or delta[-1]['role'] != 'assistant' or not surface['answer']
            or delta[-1]['content'] != surface['answer']):
        raise ValueError('unexpected native conversation order or final answer')
    if (surface['source_files'] != expected_prefix or
            any(c['name'] not in condition.tool_names for c in surface['tool_calls'])):
        raise ValueError('unexpected sources or tools')
    # Unknown automatic recall is distinct from an observed empty hook.
    if surface['prefetch'] is not None and not isinstance(surface['prefetch'], str):
        raise ValueError('invalid prefetch trace')
    if not isinstance(surface['model_requests'], list) or not surface['model_requests']:
        raise ValueError('missing actual answer input evidence')
    # Each request in a tool loop sees a prefix of the native conversation.
    expected = history[:-1]
    last_user = max(i for i, row in enumerate(expected) if row['role'] == 'user')
    round_prefixes, calls = _tool_rounds(history, last_user)
    captured_prefixes = []
    wire_tool_newline_removed = set()
    for request in surface['model_requests']:
        if condition.provider == 'memcal' and condition.version == MEMCAL_WEEK_VERSION:
            verify_captured_driver(request)
        if (not request.get('request_id') or not request.get('messages') or
                sorted(request['tools']) != sorted(condition.tool_names) or
                request['model'] != condition.model):
            raise ValueError('incomplete model request or unexpected model/tool surface')
        wire = [row for row in request['messages'] if row['role'] != 'system']
        if not captured_prefixes or captured_prefixes[-1] != len(wire):
            captured_prefixes.append(len(wire))
        if not last_user < len(wire) <= len(expected):
            raise ValueError('wire request omitted user context or added messages')
        for index, (saved, sent) in enumerate(zip(expected, wire)):
            role = saved['role']
            if sent['role'] != role:
                raise ValueError('wire conversation roles or tool grouping differ')
            content = saved.get('api_content') or saved.get('content') or ''
            if role == 'user':
                # Hermes may discard stale prefetch from a previous user turn.
                if sent.get('content') != content and not (
                        index < last_user and sent.get('content') == saved['content']):
                    raise ValueError('actual driver input lost or changed user context')
            elif content != (sent.get('content') or ''):
                if role != 'tool' or not _wire_tool_text(content, sent.get('content')):
                    raise ValueError('wire history omitted or altered an answer or tool result')
                wire_tool_newline_removed.add(saved['tool_call_id'])
            if role == 'tool' and saved.get('tool_call_id') != sent.get('tool_call_id'):
                raise ValueError('wire tool response changed call ID')
            if ([_tool_call_contract(call, stored=True) for call in _saved_calls(saved)] !=
                    [_tool_call_contract(call) for call in (sent.get('tool_calls') or [])]):
                raise ValueError('wire assistant tool calls differ')
    if len(wire) != len(expected):
        raise ValueError('final wire request omitted native history')
    if captured_prefixes != round_prefixes:
        raise ValueError('model requests do not follow the complete native tool groups')
    streamed = [(c['tool_call_id'], c['name'], c['input']) for c in surface['tool_calls']]
    if calls != streamed:
        raise ValueError('stream tool identity or arguments differ from native history')
    tools = [r for r in delta if r['role'] == 'tool']
    if [(r['tool_call_id'], r['tool_name'], r.get('api_content') or r['content']) for r in tools] != [
            (r['tool_call_id'], r['tool'], r['text']) for r in surface['tool_results']]:
        raise ValueError('tool result capture differs from answer-visible text')
    comparisons = {}
    tool_wire = {r['tool_call_id']: r['content'] for r in wire[last_user + 1:] if r['role'] == 'tool'}
    for result in surface['tool_results']:
        ident = result['tool_call_id']
        witness = surface.get('tool_boundaries', {}).get(ident, {})
        comparisons[ident] = tool_text_boundary(witness, tool_wire[ident])
    try:
        lineage = retry_activity(surface['attempts'])
    except (KeyError, ValueError, TypeError) as exc:
        lineage = {'state':'invalid', 'reason':str(exc)}
    return {**tool_activity(surface['tool_calls'], surface['tool_results']),
            'tool_text_boundaries':comparisons, 'attempt_lineage':lineage,
            'wire_tool_trailing_newline_removed':sorted(wire_tool_newline_removed)}


def checkpoint_file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_transport(turn):
    """Re-read immutable native receipts, not a caller-supplied success flag."""
    g=turn.hermes_gateway or {}
    if g.get('version')!='checkpoint-session-v1':raise GatewayLifecycleError('Missing checkpoint transport receipt')
    folder=Path(g['receipt']);ready=folder/'answer-ready.json';cleanup=folder/'cleanup.json'
    if checkpoint_file_sha(ready)!=g['answer_ready_sha256'] or checkpoint_file_sha(cleanup)!=g['cleanup_sha256']:
        raise GatewayLifecycleError('Checkpoint transport receipt changed')
    r=json.loads(ready.read_text());c=json.loads(cleanup.read_text());native=r['native_turn']
    if any(native.get(k)!=getattr(turn,k) for k in ['prompt','at','answer','session_id','tool_calls','tool_results','prefetch_context','context_isolation']):
        raise GatewayLifecycleError('Returned answer differs from atomic native capture')
    if c.get('state')!='drained' or g.get('native_drain')!='complete':raise GatewayLifecycleError('Native cleanup unverified')
    if r['context_isolation'].get('state')!='pass' or g.get('new_user_count')!=1:
        raise GatewayLifecycleError('Native isolation or sender count unverified')
    e=r.get('observation_clock') or {};d=e.get('descriptor') or {}
    from pacbench.provider_clock import audit_question
    witness={'pid':g['pid'],'start_time':g['start_time'],'process_adoption':e.get('gateway_process_adoption')}
    observed=audit_question(Path(d['home']).parent/'reuse-clock',d,turn,witness)
    if any(e.get(k)!=v for k,v in observed.items()):raise GatewayLifecycleError('Native clock evidence changed')
    if g.get('close_condition'):
        from pacbench.hermes_gateway import PRECLOSE_CONDITION as CONDITION
        from pacbench.hermes_gateway import TIMEOUT, qualified
        b=g.get('preclose')
        if g['close_condition']!=CONDITION or b!=c.get('preclose') or not b or checkpoint_file_sha(b['path'])!=b['sha256']:
            raise GatewayLifecycleError('Native pre-close receipt missing or changed')
        p=json.loads(Path(b['path']).read_text());sample=p.get('observation') or {}
        if (p.get('state')!='quiescent' or p.get('condition')!=CONDITION or p.get('session_id')!=turn.session_id or
            p.get('nonce')!=d['nonce'] or p.get('wait_bound_s')!=TIMEOUT or p.get('native_shutdown_settings_changed') is not False or
            sample.get('pid')!=g['pid'] or sample.get('start_time')!=g['start_time'] or
            sample.get('observer_sha256')!=r['pins']['reuse_observer_sha256'] or
            not qualified(sample,session=turn.session_id,nonce=d['nonce'],after_ns=p['terminal_verified_before_ns'],
                prompt_hash=hashlib.sha256(turn.prompt.encode()).hexdigest())):
            raise GatewayLifecycleError('Native pre-close quiescence proof invalid')
    users=[m['content'] for m in r['answer_visible_records'] if m['role']=='user']
    if len(users)!=g['prior_user_count']+1 or users[-1]!=turn.prompt:
        raise GatewayLifecycleError('Native transcript sender count differs')
    if g['fresh_session'] and len(users)!=1:raise GatewayLifecycleError('Cross-session user history')
    return {'state':'pass','receipt_sha256':checkpoint_file_sha(ready),'clock_nonce':d['nonce']}


def verify_checkpoint(group,proof,schedule,provider,*,allow_synthetic=False):
    """Bind native receipts and a CLOSED source-only store to this exact prefix."""
    ids=group['ready_batch_ids'];by_id={b['id']:b for b in schedule}
    if proof.get('provider')!=provider or proof.get('ingested_batch_ids')!=ids:
        raise GatewayLifecycleError('Checkpoint includes missing or future sources')
    baseline=Path(proof['baseline']);manifest=tree_manifest(baseline)
    if manifest!=proof.get('manifest'):raise GatewayLifecycleError('Closed checkpoint snapshot changed')
    if set(proof.get('ingestion_receipts',{}))!=set(ids):raise GatewayLifecycleError('Native ingestion receipt coverage missing')
    for identifier in ids:
        binding=proof['ingestion_receipts'][identifier];path=Path(binding['path'])
        if checkpoint_file_sha(path)!=binding['sha256']:raise GatewayLifecycleError('Native ingest receipt changed')
        r=json.loads(path.read_text());result=r.get('result',{})
        if (r.get('provider')!=provider or r.get('state')!='ready' or r.get('ready') is not True or
            r.get('returncode')!=0 or result.get('ready') is not True or
            r.get('source_ids')!=by_id[identifier]['source_ids'] or
            not by_id[identifier].get('document_sha256') or r.get('document_sha256')!=by_id[identifier]['document_sha256'] or
            result.get('native_errors') or result.get('model_output_truncated')):
            raise GatewayLifecycleError('Native ingestion incomplete or failed')
    # Provider-specific close/reopen witness must come from the native runtime,
    # not successful construction, queue emptiness, or a copied database alone.
    closure=proof.get('native_closure',{})
    if (closure.get('provider')!=provider or closure.get('checkpoint_sha256')!=manifest['sha256'] or
        closure.get('closed_and_reopened') is not True or closure.get('pending_work')!=[] or
        not closure.get('evidence')):
        raise GatewayLifecycleError('Native closed/reopened checkpoint evidence absent')
    for binding in closure['evidence']:
        if checkpoint_file_sha(binding['path'])!=binding['sha256']:raise GatewayLifecycleError('Native closure evidence changed')
    from pacbench.workflows.turn_evidence import COLLECTOR_PROTOCOL as SOURCE_PROTOCOL
    if proof.get('protocol') != SOURCE_PROTOCOL:
        if not allow_synthetic or proof.get('condition')!='offline-synthetic-storage-fixture':
            raise GatewayLifecycleError('Native source snapshot qualification absent; synthetic readiness is not production proof')
    else:
        if provider in {'memcal','reme'}:
            verify_collector_snapshot(provider,proof,schedule,ids,manifest)
            return manifest['sha256']
        if provider!='mem0':raise GatewayLifecycleError('Native source snapshot adapter is not qualified for this provider')
        from pacbench.gateway_reuse import MEM0_PLUGIN_SHA256
        from pacbench.hermes_gateway import _live
        for binding in closure['evidence']:
            native=json.loads(Path(binding['path']).read_text());reopened=native.get('native_reopen',{})
            if (native.get('protocol')!=SOURCE_PROTOCOL or native.get('provider')!=provider or
                native.get('checkpoint_sha256')!=manifest['sha256'] or native.get('ingested_batch_ids')!=ids or
                native.get('source_only') is not True or native.get('pending_work')!=[] or
                native.get('pins',{}).get('mem0_sdk')!='2.2.1' or
                native.get('pins',{}).get('mem0_plugin_sha256')!=MEM0_PLUGIN_SHA256 or
                not native.get('native_ingest_worker_pids') or any(_live(pid) for pid in native['native_ingest_worker_pids']) or
                reopened.get('provider')!='mem0' or reopened.get('sqlite_integrity')!='ok' or
                reopened.get('history_namespace_verified') is not True or reopened.get('qdrant_namespace_verified') is not True or
                reopened.get('memory_mutation_requested') is not False or reopened.get('llm_inference_requested') is not False):
                raise GatewayLifecycleError('Native store reopen/quiescence evidence invalid')
            clocks=native.get('source_and_billing_clock_proofs',[])
            from datetime import datetime

            from pacbench.workflows.source_plan import processing_at
            if [row.get('batch_id') for row in clocks]!=ids:
                raise GatewayLifecycleError('Native source clock coverage absent')
            for row in clocks:
                expected=datetime.fromisoformat(processing_at(by_id[row['batch_id']]['at'],provider,'America/New_York'))
                if (abs(datetime.fromisoformat(row['billing_at']).timestamp()-row['real_epoch'])>5 or
                    datetime.fromisoformat(row['provider_at'])<expected or
                    datetime.fromisoformat(row['provider_at'])>=datetime.fromisoformat(group['questions'][0]['at'])):
                    raise GatewayLifecycleError('Native source visibility or real billing-clock evidence invalid')
        for identifier in ids:
            calls=proof['ingestion_receipts'][identifier].get('native_calls',{});document=proof.get('input_documents',{}).get(identifier,{})
            if checkpoint_file_sha(calls['path'])!=calls['sha256'] or checkpoint_file_sha(document['path'])!=by_id[identifier]['document_sha256']:
                raise GatewayLifecycleError('Native submission evidence changed')
            text=Path(document['path']).read_text();records=json.loads(Path(calls['path']).read_text())
            if not any(text in m.get('content','') for c in records for m in c['request'].get('messages',[]) if m.get('role')=='user'):
                raise GatewayLifecycleError('Native input is incomplete or differs from frozen document')
    return manifest['sha256']


def freeze_inputs(preparation,schedule,provider,expected):
    """Bind existing r2 files; never render, repair, or migrate historical source.

    expected contains independently retained raw file hashes, not a hash made up
    by a runtime after it has already ingested the corpus.
    """
    from pacbench.corpus.contract import load_contract
    from pacbench.corpus.r2 import checkpoint_plan
    from pacbench.judging.recovery import load_questions
    root=Path(preparation)
    paths=['bundle.json','resolved-spec.json','evaluation-plan.json']
    resolved=json.loads((root/'resolved-spec.json').read_text())
    for row in resolved['checks']:
        paths.extend(f'evaluations/{row["id"]}/{name}.json' for name in ['contract','followup-plan'])
    if set(expected)!=set(paths) or any(checkpoint_file_sha(root/p)!=expected[p] for p in paths):
        raise ValueError('Frozen r2 input identity/hash differs')
    bundle=json.loads((root/'bundle.json').read_text())
    messages=[m for life in bundle['lives'].values() for m in life['messages']]
    if len(messages)!=142 or len({m['source_id'] for m in messages})!=142 or len(resolved['checks'])!=42:
        raise ValueError('Frozen r2 source/question denominator differs')
    all_ids=[i for b in schedule for i in b['source_ids']]
    if len(all_ids)!=142 or set(all_ids)!={m['source_id'] for m in messages}:
        raise ValueError('Frozen source schedule missing, duplicated or foreign messages')
    documents={};checks=[]
    for row in resolved['checks']:
        directory=root/'evaluations'/row['id'];documents[row['id']]=json.loads((directory/'contract.json').read_text())
        contract=load_contract(directory/'contract.json');policy=load_questions(directory/'followup-plan.json',contract)
        checks.append((row,contract,policy))
    for batch in schedule:
        source=root/batch['source_file']
        if checkpoint_file_sha(source)!=batch.get('document_sha256'):
            raise ValueError('Frozen source document content differs')
    plan=checkpoint_plan(resolved,schedule,provider,contracts=documents)
    if plan['criteria']!=188:raise ValueError('Frozen r2 criterion denominator differs')
    plan['frozen_inputs']={'protocol':'r2-input-file-bindings-v1','sha256':hashlib.sha256(
        json.dumps(expected,sort_keys=True).encode()).hexdigest(),'messages':142,'questions':42,'criteria':188}
    return checks,plan


COLLECTOR_PROTOCOL='native-source-snapshot-v1'


def verify_collector_snapshot(provider,proof,schedule,ids,manifest):
    """Provider-specific qualification after common receipt/hash validation."""
    from pacbench.provider_versions import (
        MEMCAL_CONTEXT_REVISION,
        MEMCAL_OPERATIONS_REVISION,
        MEMCAL_REVISION,
    )
    from pacbench.providers.reme import PINS, audit_reme_resource_receipt
    by_id={b['id']:b for b in schedule}
    expected_rows=sum(len(by_id[i]['source_ids']) for i in ids)
    for binding in proof['native_closure']['evidence']:
        native=json.loads(Path(binding['path']).read_text());reopened=native.get('native_reopen',{})
        if (native.get('protocol')!=COLLECTOR_PROTOCOL or native.get('provider')!=provider or
            native.get('checkpoint_sha256')!=manifest['sha256'] or native.get('ingested_batch_ids')!=ids or
            native.get('source_only') is not True or native.get('pending_work')!=[] or
            reopened.get('provider')!=provider or reopened.get('llm_inference_requested') is not False or
            reopened.get('memory_mutation_requested') is not False):
            raise GatewayLifecycleError('Native collector/store reopen evidence invalid')
        if provider=='memcal':
            pins=native.get('pins',{})
            valid_pin=pins.get('memcal') in {MEMCAL_CONTEXT_REVISION,MEMCAL_OPERATIONS_REVISION} or (
                pins.get('memcal')==MEMCAL_REVISION and pins.get('memcal_baseline')=='stable-0.8.11')
            if (not valid_pin or reopened.get('sqlite_integrity')!='ok' or
                reopened.get('archive_rows')!=expected_rows or native.get('expected_archive_rows')!=expected_rows or
                any(reopened.get(k)!=0 for k in ['pending_spool','failed_runs','incomplete_collection_sources'])):
                raise GatewayLifecycleError('Native repaired MemCal archive/processing coverage incomplete')
            readback=native.get('archive_source_readback',{})
            if (readback.get('protocol')!='native-archive-source-comparison-v1' or readback.get('compared')!=expected_rows or
                readback.get('all_identities_and_times_match') is not True or
                [w.get('source_id') for w in readback.get('rows',[])]!=[sid for i in ids for sid in by_id[i]['source_ids']]):
                raise GatewayLifecycleError('Native MemCal archive source comparison absent')
        elif provider=='reme':
            cards=reopened.get('cards',[]);pids=native.get('native_ingest_worker_pids',[])
            if (native.get('pins',{}).get('reme')!=PINS['reme'][1] or native.get('expected_cards')!=len(ids) or
                len(cards)!=len(ids) or reopened.get('jobs_started') is not False or
                not pids or any(_live(pid) for pid in pids) or
                native.get('latest_shutdown',{}).get('forced_kill') is not False or
                native.get('latest_shutdown',{}).get('native_shutdown_complete') is not True or
                native.get('latest_shutdown',{}).get('returncode') not in (0,-15) or
                any(not c.get('chunk_ids') or not c.get('keyword_hit_ids') or
                    not set(c['keyword_hit_ids']).issubset(c['chunk_ids']) for c in cards)):
                raise GatewayLifecycleError('Native ReMe resource/index/quiescence coverage incomplete')
            log=native['latest_shutdown']['log_slice']
            with Path(log['path']).open('rb') as stream:
                stream.seek(log['offset']);segment=stream.read(log['length'])
            if hashlib.sha256(segment).hexdigest()!=log['sha256']:
                raise GatewayLifecycleError('Native ReMe shutdown log evidence changed')
        else:raise GatewayLifecycleError('Unqualified native collector provider')
    for identifier in ids:
        document=proof.get('input_documents',{}).get(identifier,{})
        path=Path(document['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=by_id[identifier]['document_sha256']:
            raise GatewayLifecycleError('Native collector source document changed')
        result=json.loads(Path(proof['ingestion_receipts'][identifier]['path']).read_text())['result']
        if provider=='memcal':
            if (result.get('collector_protocol')!='native-cli-collection-v1' or
                result.get('processing',{}).get('errors') or result.get('collection',{}).get('error')):
                raise GatewayLifecycleError('Native MemCal collection/Dream readiness unverified')
        else:
            if audit_reme_resource_receipt(result,identifier,path.read_text(),by_id[identifier]['source_ids']):
                raise GatewayLifecycleError('Native ReMe resource/card/index binding invalid')
            resource=Path(proof['baseline'])/result['resource_path'];card=Path(proof['baseline'])/result['card_path']
            if resource.read_bytes()!=path.read_bytes() or hashlib.sha256(card.read_bytes()).hexdigest()!=result['card_sha256']:
                raise GatewayLifecycleError('Native ReMe persisted source/card bytes differ')
            if not any(c['card_path']==result['card_path'] and c['source_resource']=='[['+result['resource_path']+']]'
                for binding in proof['native_closure']['evidence']
                for c in json.loads(Path(binding['path']).read_text())['native_reopen']['cards']):
                raise GatewayLifecycleError('Native ReMe reopened card provenance differs')


def extend_history(history, turn):
    return history + [{'role': 'user', 'content': judge_question(turn)},
                      {'role': 'assistant', 'content': turn.answer}]


def extend_path(inherited, turn, provenance_version='source-provenance-v1'):
    profile = tool_profile(turn, provenance_version=provenance_version)
    return {'source_fallback': inherited['source_fallback'] or profile['source_fallback_attempted'],
            'unclassified': inherited['unclassified'] or bool(profile['unclassified_operations'])}


def session_key(row, context):
    return row.get('session') or ('shared' if context == 'shared' else row['id'])


def fresh_state():
    return {'history': [], 'previous': None, 'session': None,
            'session_started_at': None,
            'path': {'source_fallback': False, 'unclassified': False}, 'failed': None}


def advance(state, turn, directory, provenance_version='source-provenance-v1'):
    if state['session_started_at'] is None:
        state['session_started_at'] = turn.session_started_at or turn.at
    state['history'] = extend_history(state['history'], turn)
    state['path'] = extend_path(state['path'], turn, provenance_version)
    state['previous'], state['session'] = turn, turn.session_id
    if 'strict' in state:
        from pacbench.source_policy import classify_retention as classify
        state['strict']=classify(state.get('provider_name',directory.parents[1].name),turn,state['strict'])['state']
    follow = directory / 'followups/combined/turn.json'
    if follow.exists():
        next_turn = harness.TurnResult(**json.loads(follow.read_text()))
        state['history'] = extend_history(state['history'], next_turn)
        state['path'] = extend_path(state['path'], next_turn, provenance_version)
        state['previous'] = next_turn
        if 'strict' in state:
            state['strict']=classify(state.get('provider_name',directory.parents[1].name),next_turn,state['strict'])['state']


def diagnostic_inputs(turn, contract, evidence_by_id, *, prior_turn=None, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION):
    """Only captured inputs visible to this answer; native background traces are excluded."""
    limit = 16000
    def bounded(identifier, value, *, truncated=False):
        value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        return {'id': identifier, 'text': value[:limit], 'truncated': bool(truncated) or len(value) > limit}
    visible = []
    for current, prefix in ((prior_turn, 'initial-'), (turn, '')):
        if current is None: continue
        if current.prefetch_context is not None:
            visible.append(bounded(prefix + 'prefetch', current.prefetch_context))
        for i, result in enumerate(current.tool_results[:len(current.tool_calls)]):
            if isinstance(result, dict) and isinstance(result.get('text'), str):
                captured = bounded(f'{prefix}tool-result-{i + 1}', result['text'], truncated=result.get('truncated', False))
                for key in ('success', 'policy_blocked'):
                    if key in result: captured[key] = result[key]
                visible.append(captured)
    ids = list(dict.fromkeys(e for claim in contract.claims for e in claim.evidence))
    sources = [bounded(identifier, evidence_by_id[identifier]) for identifier in ids if identifier in evidence_by_id]
    return {'answer_visible': visible, 'source_evidence': sources,
            'source_scope': 'partial: expected-claim citations only',
            'missing_source_ids': [identifier for identifier in ids if identifier not in evidence_by_id],
            'answer_visible_scope': ('provider evidence returned to Hermes: captured prefetch and direct tool results only; user prompts, tool arguments and background traces excluded' if diagnostic_version != LEGACY_DIAGNOSTIC_VERSION else 'captured prefetch and direct tool results only; background traces excluded'),
            'per_item_character_limit': limit}
