"""Integrity checks for direct document/native-source ingestion, no answer scoring."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from .document_ingestion import VERSION, SUPPORTED_VERSIONS, daily_batches, document_text, processing_at, validate_evaluation_clocks, ready_sources


def same_submitted_text(observed,text):
    # Native storage trims the export's single framing LF. Message bodies and
    # all internal whitespace must still match; never normalize their text.
    return isinstance(observed,str) and (observed==text or text.endswith('\n') and observed==text[:-1])


def audit_reme_resource_receipt(result, day, text, source_ids):
    """Require actual native action and indexed source-linked card witnesses."""
    errors=[]
    digest=hashlib.sha256(text.encode()).hexdigest()
    relative=f'resource/{day}/source-{digest}.txt'
    receipt=result.get('native_result',{})
    response=receipt.get('response',{})
    items=response.get('metadata',{}).get('results',[])
    matches=[i for i in items if i.get('path')==relative]
    metadata=(matches[0].get('metadata') or {}) if len(matches)==1 else {}
    nodes=receipt.get('hook_metadata',{}).get('observed_index_nodes',[])
    card=result.get('card_text')
    if (result.get('ingestion_condition')!='native-resource-document-extraction-v1' or
            result.get('resource_path')!=relative or result.get('document_sha256')!=digest or
            result.get('stored_source_sha256')!=digest or result.get('source_ids')!=source_ids or
            result.get('source_readback_exact') is not True or result.get('manual_job_invocations')!=0 or
            result.get('native_defaults') is not True):
        errors.append('ReMe native document delivery/source binding is unproven')
    if (receipt.get('protocol')!='reme-native-resource-observer-v1' or
            receipt.get('processing_forced') is not False or response.get('success') is not True or
            receipt.get('response_sha256')!=hashlib.sha256(json.dumps(response,sort_keys=True,ensure_ascii=False).encode()).hexdigest() or
            len(matches)!=1 or matches[0].get('success') is not True or
            metadata.get('action')!='added' or metadata.get('source_resource')!=f'[[{relative}]]' or
            'error' in (metadata.get('index') or {})):
        errors.append('ReMe native resource processing did not complete')
    if (not isinstance(card,str) or hashlib.sha256(card.encode()).hexdigest()!=result.get('card_sha256') or
            f'[[{relative}]]' not in (card or '') or result.get('card_path')!=metadata.get('path') or
            not str(result.get('card_path','')).startswith(f'daily/{day}/') or
            receipt.get('hook_metadata',{}).get('index_observation_complete') is not True or
            len(nodes)!=1 or nodes[0].get('path')!=result.get('card_path') or not nodes[0].get('chunk_ids') or
            nodes[0].get('front_matter',{}).get('source_resource')!=f'[[{relative}]]'):
        errors.append('ReMe generated memory card/index provenance is unproven')
    return errors


def audit_supermemory_receipt(result, day, text):
    statuses=result.get('processing_status',[])
    final=statuses[-1] if statuses else {}
    errors=[]
    if result.get('delivery_options') is not None and result['delivery_options']!={'dreaming':'instant'}:
        errors.append('Supermemory ingestion mode differs from the declared daily-batch connector')
    if (final.get('status')!='done' or
            final.get('dreaming_status',final.get('dreamingStatus'))!='done'):
        errors.append('Supermemory memory consolidation did not complete')
    v5=result.get('native_api_protocol')=='supermemory-v5-documents-v1'
    system=final.get('native_system') or {}
    native_phase=system.get('dreaming_status',system.get('dreamingStatus'))
    witness=final.get('native_completion_witness') or {}
    if v5 and native_phase is None:
        content=final.get('content')
        if (witness.get('protocol')=='supermemory-native-v3-completion-observer-v1'
                and witness.get('namespace')==result.get('container_tag')
                and witness.get('public_id')==day and witness.get('native_id')
                and witness.get('status')=='done' and witness.get('processing_forced') is False
                and witness.get('metadata')==final.get('metadata') and isinstance(content,str)
                and witness.get('content_sha256')==hashlib.sha256(content.encode()).hexdigest()):
            native_phase=witness.get('dreaming_status')
    identity_ok=(final.get('id')==day and final.get('request_namespace')==result.get('request_namespace')==result.get('container_tag')
                 and (final.get('metadata') or {}).get('collected_at')
                 and (final.get('native_system') or {}).get('status')=='done'
                 and native_phase=='done') if v5 else (
                     final.get('custom_id',final.get('customId'))==day and result.get('container_tag') in final.get('container_tags',final.get('containerTags',[])))
    if (not final.get('id') or final.get('id')!=result.get('result',{}).get('id') or not identity_ok or
            not same_submitted_text(final.get('content'),text) or
            not result.get('container_tag')):
        errors.append('Supermemory completion receipt differs from submitted source or namespace')
    return errors


def openviking_observer_state(value):
    """Validate the native post-wait snapshot; distinguish busy from malformed/error."""
    result=value.get('result') or {}
    queues=(result.get('status') or {}).get('queues')
    if (value.get('status')!='ok' or result.get('is_healthy') is not True
            or result.get('has_errors') is not False or not isinstance(queues,list)
            or not all(isinstance(q,dict) for q in queues)
            or not {'Embedding','Semantic'}.issubset({q.get('queue') for q in queues})):
        return 'invalid'
    counters=[q.get(k) for q in queues for k in ('pending','in_progress','errors')]
    if any(type(v) is not int or v<0 for v in counters) or any(q['errors'] for q in queues):
        return 'invalid'
    return 'busy' if any(q['pending'] or q['in_progress'] for q in queues) else 'ready'


def openviking_generated_artifacts(workspace):
    """Read-only check for native unresolved summary placeholders after drain."""
    files=[]
    for name in ('.overview.md','.abstract.md'):
        for path in sorted((Path(workspace)/'viking').rglob(name)):
            body=path.read_bytes()
            failed=any(marker in body for marker in (
                b'[Directory overview is not generated]',b'[Directory overview is not ready]',b'[Directory abstract is not ready]'))
            files.append({'path':str(path.relative_to(workspace)),
                'sha256':hashlib.sha256(body).hexdigest(),'failed_placeholder':failed})
    return {'state':'complete' if files and not any(f['failed_placeholder'] for f in files) else 'incomplete',
        'files':files,'scope':'Native generated summary artifacts read without alteration; queue health alone can conceal model failures.'}


OPENVIKING_FAILURE_MARKERS=(
    'Failed to generate summary for','VLM not available, using empty summary',
    'Failed to generate partial overview batch','Failed to merge partial overviews',
    'Failed to generate overview for','Failed to dispatch directory')


def openviking_failure_log(path):
    path=Path(path)
    if not path.is_file():return {'state':'unproven','failures':[]}
    failures=[{'line':i+1,'marker':marker} for i,line in enumerate(path.read_text(errors='replace').splitlines())
              for marker in OPENVIKING_FAILURE_MARKERS if marker in line]
    return {'state':'incomplete' if failures else 'complete','failures':failures,
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def openviking_response_evidence(rows):
    results=[]
    for row in rows:
        v=row.get('response_validation') or {}
        valid=(row.get('http_status')==200 and v.get('body_complete') is True
            and v.get('envelope_errors')==[] and v.get('assistant_nonwhitespace_characters',0)>0
            and v.get('finish_reasons')==['stop'])
        results.append({'id':row['id'],'complete':valid,'response_validation':v})
    return {'state':'complete' if results and all(r['complete'] for r in results) else 'incomplete',
        'requests':results,'scope':'Every request in the isolated import; native recovery would need separately reviewed evidence.'}


def audit_openviking_receipt(result):
    native=result.get('result',{}).get('result',{})
    queues=native.get('queue_status')
    if (native.get('status')!='success' or native.get('errors') or not native.get('root_uri') or
            not isinstance(queues,dict) or not queues or any(
                not isinstance(q,dict) or q.get('errors') or q.get('error_count',0)
                for q in queues.values())):
        return ['OpenViking native semantic/vector processing did not succeed']
    if result.get('completion_protocol') is not None:
        global_value=result.get('native_global_queue_completion') or {}
        global_queues=global_value.get('result')
        if (result['completion_protocol']!='openviking-global-import-readiness-v4'
                or global_value.get('status')!='ok' or not isinstance(global_queues,dict)
                or not global_queues or any(not isinstance(q,dict) or q.get('error_count')!=0
                    or q.get('errors')!=[] for q in global_queues.values())
                or openviking_observer_state(result.get('native_global_queue_observation') or {})!='ready'):
            return ['OpenViking global processing completion is unproven']
        artifacts=result.get('native_generated_artifact_check') or {}
        files=artifacts.get('files')
        if (artifacts.get('state')!='complete' or not isinstance(files,list) or not files
                or any(not isinstance(f,dict) or f.get('failed_placeholder') is not False
                    or not f.get('sha256') or not f.get('path') for f in files)):
            return ['OpenViking generated summaries contain unresolved processing failures']
        log=result.get('native_failure_log_check') or {}
        responses=result.get('native_model_response_check') or {}
        if (log.get('state')!='complete' or log.get('failures')!=[]
                or responses.get('state')!='complete' or not responses.get('requests')
                or any(r.get('complete') is not True for r in responses['requests'])):
            return ['OpenViking model/subtask completion is unproven']
    return []


def audit_honcho_receipt(batch, result, previous_work=None, previous_rosters=None):
    """Independently bind native acknowledgments and readiness to frozen sources."""
    if previous_work is None:previous_work={}
    errors=[];seen=[];sessions=set();minimum={}
    if result.get('membership_protocol') is not None:
        from provider_adapters.honcho_enrollment import SUPPORTED, source_plans, validate_windows, validate_roster_continuity
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
            from provider_adapters.honcho_enrollment import source_peer_id, LEGACY_PROTOCOL, SUPPORTED
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


def audit_ingestion(root, arm, messages, summary, *, import_only=False):
    errors=[]
    declared_clock = summary.get('provider_clocks',{}).get(arm.name)
    if declared_clock is not None:
        if arm.name != 'honcho':
            errors.append('Unsupported declared native clock provider')
        else:
            from provider_adapters.honcho_clock import audit_evidence
            errors.extend(audit_evidence(arm, declared_clock))
    def check(ok,msg):
        if not ok:errors.append(msg)
    schedule=summary['ingestion_schedule']
    version=summary.get('ingestion_version',VERSION)
    check(version in SUPPORTED_VERSIONS,'unsupported ingestion version')
    batches=daily_batches(messages,summary['process_timezone'] or 'America/New_York')
    check(len(schedule)==len(batches),'daily ingestion schedule incomplete')
    honcho_work={};honcho_rosters={};cognee_prior=[]
    cumulative=[]
    cumulative_mail=[]
    for row,(day,batch) in zip(schedule,batches):
        cumulative+=[m for m in batch if m['channel']=='imessage']
        cumulative_mail+=[m for m in batch if m['channel']=='email']
        source=root/'sources'/day
        meta=json.loads((source/'source.json').read_text())
        at=max((m['collected_at'] for m in batch),key=datetime.fromisoformat)
        ids=[m['source_id'] for m in batch]
        text=document_text(batch,at,version=version)
        check(row['id']==day and row['at']==at and row['source_ids']==ids,'source availability schedule mismatch')
        check(row['file']==f'ingestion/{day}.json' and row['source_file']==f'sources/{day}/messages.txt','unsafe or unexpected ingestion path')
        check(hashlib.sha256((source/'source.json').read_bytes()).hexdigest()==row['source_manifest_sha256'],'source manifest hash mismatch')
        check(meta['version']==version and meta['source_ids']==ids and meta['at']==at,'source manifest differs from frozen observations')
        check((source/'messages.txt').read_text()==text and json.loads((source/'messages.json').read_text())==batch,'delivered source text differs from frozen observations')
        check(hashlib.sha256(text.encode()).hexdigest()==meta['document_sha256'],'source document hash mismatch')
        for name,sha in meta.get('fixture_hashes',{}).items():
            check(Path(name).name==name,'unsafe fixture path')
            check(hashlib.sha256((source/name).read_bytes()).hexdigest()==sha,'source fixture changed')
        if meta.get('fixture'):
            manifest=json.loads((source/'chat.fixture.json').read_text())
            check(set(manifest['messages'])=={m['source_id'] for m in cumulative},'source fixture has missing/future messages')
            with sqlite3.connect(f'file:{source/"chat.db"}?mode=ro',uri=True) as db:
                check(db.execute('SELECT count(*) FROM message').fetchone()[0]==len(cumulative),'fixture message count mismatch')
                for m in cumulative:
                    recorded=manifest['messages'][m['source_id']]
                    rowdata=db.execute('SELECT guid,text,is_from_me FROM message WHERE ROWID=?',(recorded['rowid'],)).fetchone()
                    check(rowdata==(recorded['guid'],m['text'],int(m['from_owner'])),'fixture text/owner/GUID differs from source')
                check(not db.execute('SELECT m.ROWID FROM message m LEFT JOIN chat_message_join j ON j.message_id=m.ROWID WHERE j.chat_id IS NULL').fetchall(),'orphan source messages')
        if meta.get('mailbox'):
            from email import message_from_bytes
            from email.utils import parsedate_to_datetime,parseaddr
            from .email_fixture import mail_address
            mailbox=json.loads((source/'mailbox.json').read_text())
            check([r['source'] for r in mailbox['messages']]==cumulative_mail,
                  'mailbox has missing, changed or future source emails')
            for item in mailbox['messages']:
                # SMTP is a byte protocol. Parsing a Unicode string with the
                # compat parser escapes 8-bit bodies (e.g. an em dash).
                m=item['source'];mail=message_from_bytes(item['rfc822'].encode('utf-8'))
                check(parseaddr(mail['From'])[1]==mail_address(m['sender']), 'email sender differs from source')
                check(parsedate_to_datetime(mail['Date'])==datetime.fromisoformat(m['written_at']),
                      'email date differs from source')
                check(mail.get_payload(decode=True).decode(mail.get_content_charset()).replace('\r\n','\n').strip()==m['text'].replace('\r\n','\n').strip(),
                      'email body differs from source')
                check(item['folder']==('Sent' if m['from_owner'] else 'INBOX'), 'email ownership folder mismatch')
        record=json.loads((arm/row['file']).read_text())
        if record.get('delivery_journal'):
            delivery=record['delivery_journal'];path=arm/delivery['path']
            check(path.resolve().is_relative_to(arm.resolve()),'Unsafe native delivery receipt path')
            if path.resolve().is_relative_to(arm.resolve()):
                from provider_adapters.document_connector import verify_honcho_delivery
                try:
                    check(hashlib.sha256(path.read_bytes()).hexdigest()==delivery['sha256'],'Native delivery manifest changed')
                    verify_honcho_delivery(path,complete=record.get('ready') is True)
                except (OSError,ValueError,KeyError,TypeError):errors.append('Native delivery receipt is missing or invalid')
        check(record['provider']==arm.name and record['version']==version and record['at']==at and record['source_ids']==ids,'ingestion acknowledgement binding mismatch')
        check(record['document_sha256']==meta['document_sha256'],'provider document hash mismatch')
        check(record['hermes_ingestion_turns']==0,'Hermes used for ingestion')
        check(record['ready'] is True and record['state']=='ready' and record.get('returncode')==0,'native processing did not complete')
        check(record['processing_at']==processing_at(at,arm.name,summary['process_timezone'] or 'America/New_York',version=version),'native processing clock mismatch')
        if arm.name=='honcho':
            membership = summary.get('provider_membership_protocols',{}).get('honcho')
            if membership is not None:
                from provider_adapters.honcho_enrollment import SUPPORTED, source_plans
                check(membership in SUPPORTED and
                      record.get('membership_protocol') == record.get('result',{}).get('membership_protocol') == membership,
                      'Honcho native membership protocol differs from declared condition')
                enrollment = record.get('enrollment',{})
                check(enrollment.get('protocol') == membership and enrollment.get('state') == 'enrolled' and
                      enrollment.get('source_messages_submitted') == 0 and
                      enrollment.get('source_sha256') == hashlib.sha256((source/'messages.json').read_bytes()).hexdigest(),
                      'Honcho chronological enrollment is missing or bound to different source')
                try:
                    check(enrollment.get('plans') == source_plans(batch, {'peerName':'user','aiPeer':'hermes'},protocol=membership),
                          'Honcho enrolled roster differs from original source membership')
                except (ValueError,KeyError,TypeError):
                    errors.append('Honcho source membership cannot be qualified')
            errors.extend(audit_honcho_receipt(batch,record.get('result',{}),honcho_work,honcho_rosters))
            declared = summary.get('provider_clocks',{}).get('honcho')
            check(record.get('result',{}).get('clock_protocol') == declared,
                  'Honcho native clock differs from declared trial condition')
        if arm.name=='supermemory':
            errors.extend(audit_supermemory_receipt(record.get('result',{}),day,text))
            config=arm/'document-ingestion-config.json'
            if config.exists():
                check(record.get('result',{}).get('delivery_options')==
                      {'dreaming':json.loads(config.read_text()).get('dreaming')},
                      'Supermemory delivery differs from its saved native SDK configuration')
        if arm.name=='openviking':
            errors.extend(audit_openviking_receipt(record.get('result',{})))
        if arm.name=='reme':
            errors.extend(audit_reme_resource_receipt(record.get('result',{}),day,text,ids))
        if arm.name=='cognee':
            native=record.get('result',{})
            setup=json.loads((arm/'native-server-setup.json').read_text())
            check(native.get('protocol') in {'cognee-native-document-qualification-v1','cognee-native-incremental-document-v2'} and
                  native.get('source_sha256')==native.get('document_sha256')==meta['document_sha256'] and
                  native.get('source_ids')==ids and native.get('source_at')==at and
                  native.get('dataset')==setup.get('dataset') and native.get('service')==setup.get('endpoint') and
                  bool(native.get('dataset_id')) and bool(native.get('data_id')) and
                  native.get('pipeline')=='cognify_pipeline' and
                  native.get('observed_statuses',[])[-1:] == ['DATASET_PROCESSING_COMPLETED'] and
                  native.get('stored_source_verified') is True and native.get('processing_complete') is True,
                  'Cognee native extraction/source completion is unproven')
            if native.get('protocol')=='cognee-native-incremental-document-v2':
                snapshots=native.get('item_completion_observations',[])
                final=snapshots[-1] if snapshots else {}
                items=final.get('items',[])
                captures=native.get('native_captures',[])
                for capture in captures:
                    path=(arm/capture['turn_file']).resolve()
                    check(path.is_relative_to((arm/'turns').resolve()) and capture.get('protocol')=='cognee-native-observed-turn-qa-v1' and
                          hashlib.sha256(path.read_bytes()).hexdigest()==capture.get('turn_sha256'),
                          'Cognee additional document is not bound to an observed native turn')
                expected_ids={p['data_id'] for p in [*cognee_prior,*captures]}|{native.get('data_id')}
                check(native.get('prior_sources')==cognee_prior and
                      len(items)==len(expected_ids) and {i.get('id') for i in items}==expected_ids and
                      all(i.get('completed') is True for i in items) and
                      final.get('total')==final.get('completed')==len(expected_ids) and final.get('pending')==0,
                      'Cognee incremental completion is not bound to this new source item')
            elif cognee_prior:
                errors.append('Single-document Cognee proof cannot certify incremental ingestion')
            cognee_prior.append({'data_id':native.get('data_id'),'source_sha256':native.get('source_sha256')})
        if arm.name=='memos':
            from provider_adapters.reme_memos_documents import payload
            expected,_=payload('memos',source)
            native=record.get('result',{});actual=native.get('native_readback',{})
            check(native.get('ingestion_condition')=='native-raw-document-import-v1' and
                  native.get('retrieval_credit')=='source-assisted' and native.get('extraction_performed') is False and
                  native.get('document_sha256')==meta['document_sha256'] and native.get('source_ids')==ids and
                  native.get('native_readback_exact') is True and
                  all(actual.get(key)==value for key,value in expected['traces'][0].items()),
                  'MemOS native raw-document import/readback is unproven')
        if arm.name=='memcal' and any(m['channel']=='email' for m in batch):
            result=record.get('result',{})
            check(result.get('email_ingest',{}).get('read')==sum(m['channel']=='email' for m in batch),
                  'native email capture acknowledgement missing')
            check(any(c['operation']=='FETCH' for c in result.get('email_transport',{}).get('commands',[])),
                  'native email ingestion lacks real IMAP fetch evidence')
    check(not list((arm/'exposures').glob('*.json')) and not list(arm.glob('exposure-*.json')),'unexpected Hermes exposure records')
    check(sorted(p.name for p in (arm/'ingestion').glob('*.json'))==sorted(Path(r['file']).name for r in schedule),'unexpected or missing ingestion records')
    isolation=json.loads((arm/'pre-recall-isolation.json').read_text())
    # Independently inspect actual native session messages. Saved declarations
    # alone cannot establish that raw documents were kept out of Hermes chat.
    turns=[]
    for path in [arm/'recall.json',*(arm/'turns').glob('*/turn.json'),
                 *(arm/'turns').glob('*/followups/combined/turn.json'),arm/'followups/combined/turn.json']:
        if path.exists():turns.append(json.loads(path.read_text()))
    resumed=any(json.loads(p.read_text()).get('protocol')=='scored-prefix-retained-store-v1'
                for p in (arm/'scored-resumes').glob('*/resume.json'))
    if resumed:
        # Aborted attempts remain part of the actual Hermes session database,
        # even though their failed questions are retried. Attribute them rather
        # than pretending their native conversations disappeared. This does not
        # promote failed attempts to grades or alter the frozen denominator.
        for path in (arm/'scored-resumes').glob('*/tail-turns/*/turn.json'):
            value=json.loads(path.read_text())
            from .harness import question_prompt
            frozen=json.loads((root/'evaluations'/path.parent.name/'contract.json').read_text())
            plan=json.loads((root/'evaluation-plan.json').read_text())
            row=next(r for r in plan['checks'] if r['id']==path.parent.name)
            check(value.get('at')==row['at'] and value.get('prompt')==question_prompt(frozen['question'],row['at'],summary['question_prompt_protocol']),
                  'Archived aborted turn differs from frozen question')
            if value.get('session_id'):turns.append(value)
    attributed={t.get('session_id') for t in turns if t.get('session_id')}
    check((not isolation['hermes_sessions_before_recall'] or resumed and
           set(isolation['hermes_sessions_before_recall'])<=attributed) and
          not any(v.strip() for v in isolation['builtin_memory_before_recall'].values()),
          'Hermes source conversation or builtin facts existed before recall')
    # The exact database checks below still require every native session and
    # user message to match an observed scored or archived aborted turn. A
    # recovered boundary is session attribution, not a fabricated empty receipt.
    expected={(t['session_id'],t['prompt']) for t in turns}
    if import_only:
        check(summary.get('mode')=='native-import-validation','Import-only audit requires explicit qualification scope')
        check(not turns,'Import-only validation contains benchmark turns')
        check(not list((root/'model-calls').glob('*.json')),'Import-only validation contains judge calls')
    else:
        validate_evaluation_clocks(schedule,[arm.name],[t['at'] for t in turns],summary['process_timezone'] or 'America/New_York',
                                   chronological=bool(summary.get('evaluation_plan_hash')),version=version)
    if summary.get('evaluation_plan_hash'):
        plan=json.loads((root/'evaluation-plan.json').read_text())
        for row in plan['checks']:
            directory=arm/'turns'/row['id']
            if not (directory/'turn.json').exists():continue
            state=json.loads((directory/'source-state.json').read_text())
            due=ready_sources(schedule,arm.name,row['at'],summary['process_timezone'] or 'America/New_York',version=version)
            from .document_ingestion import source_state
            expected_source_state=(source_state(row['at'],[r['id'] for r in due]) if state.get('reference_version')==2 else
                      {'at':row['at'],'ingested_batches':[r['id'] for r in due],
                       'source_ids':[sid for r in due for sid in r['source_ids']]})
            check(state==expected_source_state,'Checkpoint source state has missing/future batches')
            usage=json.loads((directory/'usage.json').read_text())
            if usage.get('state')=='captured':
                for batch in schedule:
                    record=json.loads((arm/batch['file']).read_text())
                    window=record.get('usage',{})
                    check(window.get('state')=='captured','Missing ingestion chronology evidence')
                    if window.get('state')=='captured':
                        if batch in due:
                            check(datetime.fromisoformat(window['finished'])<=datetime.fromisoformat(usage['started']),'Ingestion finished after its evaluation started')
                        else:
                            follow=directory/'followups/combined/usage.json'
                            finished=json.loads(follow.read_text())['finished'] if follow.exists() else usage['finished']
                            check(datetime.fromisoformat(window['started'])>=datetime.fromisoformat(finished),'Future ingestion began before earlier evaluation finished')
    if import_only and not (arm/'hermes-home/state.db').exists():return errors
    with sqlite3.connect(f'file:{arm/"hermes-home/state.db"}?mode=ro',uri=True) as db:
        actual=db.execute("SELECT session_id,content FROM messages WHERE role='user' ORDER BY id").fetchall()
        if resumed:
            # A cooperative stop can occur after native Hermes saved its user
            # input but before the CLI returned a TurnResult. Attribute that
            # unfinished attempt from the actual database and archived gate;
            # it remains ungraded and never becomes a fabricated answer.
            known_sessions={pair[0] for pair in expected}
            for session in {pair[0] for pair in actual}-known_sessions:
                inputs=[text for sid,text in actual if sid==session]
                if len(inputs)!=1:continue
                candidates=[]
                for status_path in (arm/'scored-resumes').glob('*/tail-turns/*/status.json'):
                    state_path=status_path.parent/'source-state.json'
                    if not state_path.exists() or (status_path.parent/'grade.json').exists():continue
                    status=json.loads(status_path.read_text())
                    if status.get('state') not in {'interrupted','ungraded','failed'}:continue
                    identifier=status_path.parent.name
                    contract_path=root/'evaluations'/identifier/'contract.json'
                    if not contract_path.exists():continue
                    row=next((r for r in json.loads((root/'evaluation-plan.json').read_text())['checks'] if r['id']==identifier),None)
                    if row is None:continue
                    from .harness import question_prompt
                    question=json.loads(contract_path.read_text())['question']
                    if inputs[0]!=question_prompt(question,row['at'],summary['question_prompt_protocol']):continue
                    state=json.loads(state_path.read_text())
                    due=ready_sources(schedule,arm.name,row['at'],summary['process_timezone'] or 'America/New_York',version=version)
                    if set(state['ingested_batches'])!={b['id'] for b in due}:continue
                    candidates.append(identifier)
                if len(set(candidates))!=1:continue
                cwd=db.execute('SELECT cwd FROM sessions WHERE id=?',(session,)).fetchone()
                if not cwd:continue
                from .context_isolation import actual_context
                if actual_context(arm/'hermes-home',session,Path(cwd[0])).get('state')!='pass':continue
                expected.add((session,inputs[0]))
                attributed.add(session)
        check(set(actual)==expected and len(actual)==len(expected),'Native Hermes user messages differ from evaluation questions')
        check({r[0] for r in db.execute('SELECT id FROM sessions')}==attributed,'Unexpected Hermes ingestion or prior sessions')
    return errors
