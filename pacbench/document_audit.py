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


def audit_supermemory_receipt(result, day, text):
    statuses=result.get('processing_status',[])
    final=statuses[-1] if statuses else {}
    errors=[]
    if result.get('delivery_options') is not None and result['delivery_options']!={'dreaming':'instant'}:
        errors.append('Supermemory ingestion mode differs from the declared daily-batch connector')
    if (final.get('status')!='done' or
            final.get('dreaming_status',final.get('dreamingStatus'))!='done'):
        errors.append('Supermemory memory consolidation did not complete')
    if (not final.get('id') or final.get('id')!=result.get('result',{}).get('id') or
            final.get('custom_id',final.get('customId'))!=day or
            not same_submitted_text(final.get('content'),text) or
            not result.get('container_tag') or
            result['container_tag'] not in final.get('container_tags',final.get('containerTags',[]))):
        errors.append('Supermemory completion receipt differs from submitted source or namespace')
    return errors


def audit_openviking_receipt(result):
    native=result.get('result',{}).get('result',{})
    queues=native.get('queue_status')
    if (native.get('status')!='success' or native.get('errors') or not native.get('root_uri') or
            not isinstance(queues,dict) or not queues or any(
                not isinstance(q,dict) or q.get('errors') or q.get('error_count',0)
                for q in queues.values())):
        return ['OpenViking native semantic/vector processing did not succeed']
    return []


def audit_honcho_receipt(batch, result, previous_work=None):
    """Independently bind native acknowledgments and readiness to frozen sources."""
    if previous_work is None:previous_work={}
    errors=[];seen=[];sessions=set();minimum={}
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
            if (actual.get('peer_id')!=('user' if original['from_owner'] else original['sender']) or
                    actual.get('content')!=original['text'] or
                    datetime.fromisoformat(actual['created_at'])!=datetime.fromisoformat(original['written_at']) or
                    any(metadata.get(k)!=v for k,v in original.items() if k!='text')):
                errors.append('Honcho acknowledged changed source text, speaker, time or metadata')
    if sorted(seen)!=sorted(expected):errors.append('Honcho acknowledgments omit or duplicate sources')
    changes=result.get('processing_status',[])
    if any(change.get('native_queue_errors') for change in changes):
        errors.append('Honcho submitted-source work completed with native errors')
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


def audit_ingestion(root, arm, messages, summary):
    errors=[]
    def check(ok,msg):
        if not ok:errors.append(msg)
    schedule=summary['ingestion_schedule']
    version=summary.get('ingestion_version',VERSION)
    check(version in SUPPORTED_VERSIONS,'unsupported ingestion version')
    batches=daily_batches(messages,summary['process_timezone'] or 'America/New_York')
    check(len(schedule)==len(batches),'daily ingestion schedule incomplete')
    honcho_work={}
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
        check(record['provider']==arm.name and record['version']==version and record['at']==at and record['source_ids']==ids,'ingestion acknowledgement binding mismatch')
        check(record['document_sha256']==meta['document_sha256'],'provider document hash mismatch')
        check(record['hermes_ingestion_turns']==0,'Hermes used for ingestion')
        check(record['ready'] is True and record['state']=='ready' and record.get('returncode')==0,'native processing did not complete')
        check(record['processing_at']==processing_at(at,arm.name,summary['process_timezone'] or 'America/New_York',version=version),'native processing clock mismatch')
        if arm.name=='honcho':
            errors.extend(audit_honcho_receipt(batch,record.get('result',{}),honcho_work))
        if arm.name=='supermemory':
            errors.extend(audit_supermemory_receipt(record.get('result',{}),day,text))
            config=arm/'document-ingestion-config.json'
            if config.exists():
                check(record.get('result',{}).get('delivery_options')==
                      {'dreaming':json.loads(config.read_text()).get('dreaming')},
                      'Supermemory delivery differs from its saved native SDK configuration')
        if arm.name=='openviking':
            errors.extend(audit_openviking_receipt(record.get('result',{})))
        if arm.name=='memcal' and any(m['channel']=='email' for m in batch):
            result=record.get('result',{})
            check(result.get('email_ingest',{}).get('read')==sum(m['channel']=='email' for m in batch),
                  'native email capture acknowledgement missing')
            check(any(c['operation']=='FETCH' for c in result.get('email_transport',{}).get('commands',[])),
                  'native email ingestion lacks real IMAP fetch evidence')
    check(not list((arm/'exposures').glob('*.json')) and not list(arm.glob('exposure-*.json')),'unexpected Hermes exposure records')
    check(sorted(p.name for p in (arm/'ingestion').glob('*.json'))==sorted(Path(r['file']).name for r in schedule),'unexpected or missing ingestion records')
    isolation=json.loads((arm/'pre-recall-isolation.json').read_text())
    check(isolation['hermes_sessions_before_recall']==[] and
          not any(v.strip() for v in isolation['builtin_memory_before_recall'].values()),
          'Hermes source conversation or builtin facts existed before recall')
    # Independently inspect actual native session messages. Saved declarations
    # alone cannot establish that raw documents were kept out of Hermes chat.
    turns=[]
    for path in [arm/'recall.json',*(arm/'turns').glob('*/turn.json'),
                 *(arm/'turns').glob('*/followups/combined/turn.json'),arm/'followups/combined/turn.json']:
        if path.exists():turns.append(json.loads(path.read_text()))
    expected={(t['session_id'],t['prompt']) for t in turns}
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
    with sqlite3.connect(f'file:{arm/"hermes-home/state.db"}?mode=ro',uri=True) as db:
        actual=db.execute("SELECT session_id,content FROM messages WHERE role='user' ORDER BY id").fetchall()
        check(set(actual)==expected and len(actual)==len(expected),'Native Hermes user messages differ from evaluation questions')
        check({r[0] for r in db.execute('SELECT id FROM sessions')}=={t['session_id'] for t in turns},'Unexpected Hermes ingestion or prior sessions')
    return errors
