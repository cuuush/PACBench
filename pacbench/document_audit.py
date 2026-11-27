"""Integrity checks for direct document/native-source ingestion, no answer scoring."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pacbench.providers.honcho import audit_honcho_receipt
from pacbench.providers.openviking import audit_openviking_receipt
from pacbench.providers.reme import audit_reme_resource_receipt
from pacbench.providers.supermemory import audit_supermemory_receipt
from pacbench.workflows.source_plan import (
    SUPPORTED_VERSIONS,
    VERSION,
    daily_batches,
    document_text,
    processing_at,
    ready_sources,
    validate_evaluation_clocks,
)


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
            from email.utils import parseaddr, parsedate_to_datetime

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
        recovered_honcho=(arm.name=='honcho' and record.get('observer_recovery_protocol')=='honcho-completed-queue-observer-timeout-v1'
            and record.get('returncode')==1 and record.get('result',{}).get('queue_cleanup_evidence',{}).get('protocol')=='honcho-enrolled-queue-success-cleanup-v1')
        check(record['ready'] is True and record['state']=='ready' and (record.get('returncode')==0 or recovered_honcho),'native processing did not complete')
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
            endpoints={setup.get('endpoint')}
            for path in (arm/'scored-resumes').glob('*/native-server-setup.json'):
                previous=json.loads(path.read_text())
                if (previous.get('dataset')==setup.get('dataset') and previous.get('model')==setup.get('model') and
                    all(previous.get(k)==setup.get(k) for k in ('version','integration_revision','backend_version'))):
                    endpoints.add(previous.get('endpoint'))
            check(native.get('protocol') in {'cognee-native-document-qualification-v1','cognee-native-incremental-document-v2'} and
                  native.get('source_sha256')==native.get('document_sha256')==meta['document_sha256'] and
                  native.get('source_ids')==ids and native.get('source_at')==at and
                  native.get('dataset')==setup.get('dataset') and native.get('service') in endpoints and
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
            from provider_adapters.memos_semantic_ready import (
                RAW,
                SEMANTIC,
                proof_valid,
            )
            from provider_adapters.reme_memos_documents import payload
            expected,_=payload('memos',source)
            native=record.get('result',{});actual=native.get('native_readback',{})
            condition=native.get('ingestion_condition')
            setup=json.loads((arm/'native-server-setup.json').read_text())
            check(condition==setup.get('ingestion_condition',RAW),'MemOS ingest condition differs from declared runtime')
            check(condition in (RAW,SEMANTIC) and
                  native.get('retrieval_credit')=='source-assisted' and native.get('extraction_performed') is False and
                  native.get('document_sha256')==meta['document_sha256'] and native.get('source_ids')==ids and
                  native.get('native_readback_exact') is True and
                  all(actual.get(key)==value for key,value in expected['traces'][0].items()),
                  'MemOS native raw-document import/readback is unproven')
            if condition == SEMANTIC:
                check(setup.get('ingestion_condition')==SEMANTIC and
                      proof_valid(native.get('semantic_readiness'),actual,meta['document_sha256']),
                      'MemOS semantic-ready condition lacks native repair/identity evidence')
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
    for t in turns:
        saved=t.get('clock_context') or {}
        identifiers=saved.get('native_network_continuation_api_input_ids',[])
        if not identifiers:continue
        from pacbench.context_isolation import (
            NATIVE_NETWORK_CONTINUATION_SHA256,
            delivery,
        )
        from pacbench.context_isolation import NATIVE_QUESTION as NATIVE
        from pacbench.context_isolation import input_rows as rows

        from .harness import TurnResult
        observed=delivery(arm,TurnResult(**t),identifiers=saved['api_input_ids'],hook_index=saved['native_query_index'],protocol=NATIVE)
        check(observed==saved and observed['state']=='pass','Native network continuation lacks its original input witness')
        if observed==saved and observed['state']=='pass':
            for row in rows(arm/'api-inputs.jsonl'):
                if row['request_id'] not in identifiers:continue
                for message in row['messages']:
                    value=message.get('content')
                    if message['role']=='user' and isinstance(value,str) and hashlib.sha256(value.encode()).hexdigest()==NATIVE_NETWORK_CONTINUATION_SHA256:
                        expected.add((t['session_id'],value))
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
            from pacbench.workflows.source_plan import source_state
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
