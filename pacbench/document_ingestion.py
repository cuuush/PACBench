"""Daily raw source delivery, without Hermes ingestion or fact extraction."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import harness
from .followups import save
from .usage import snapshot, window

VERSION = 'daily-source-documents-v3'
NIGHTLY_JSON_VERSION = 'daily-source-documents-v2'
LEGACY_VERSION = 'daily-source-documents-v1'
SUPPORTED_VERSIONS = (LEGACY_VERSION,NIGHTLY_JSON_VERSION,VERSION)
PROCESSING_TIMEOUT_S = 900
PUBLIC_FIELDS = {'source_id','channel','thread','sender','text','from_owner',
                 'written_at','arrived_at','collected_at','structured'}
BLOCKERS = {
    'holographic':'Unsupported: fact storage and regex extraction do not provide document ingestion.',
    'memori':'Excluded: Hermes documents Memori Cloud only; no local document-ingestion integration verified.',
    'retaindb':'Excluded: Hermes documents hosted storage only; the benchmark requires local memory backends.',
}
WORKER = '''
import sys
sys.path.insert(0, sys.argv[1])
from provider_adapters.document_connector import main
raise SystemExit(main(sys.argv[2:]))
'''


def remove_duplicate_stdout(record):
    """Store a parsed native JSON acknowledgment once, preserving other output."""
    lines=(record.get('stdout') or '').splitlines(keepends=True)
    last=next((i for i in range(len(lines)-1,-1,-1) if lines[i].strip()),None)
    if last is None or not isinstance(record.get('result'),dict):return False
    try:same=json.loads(lines[last])==record['result']
    except (ValueError,TypeError):return False
    if not same:return False
    record['stdout']=''.join(lines[:last]+lines[last+1:])
    record['stdout_json_in']='result'
    return True


def daily_batches(messages, timezone):
    zone = ZoneInfo(timezone)
    groups = {}
    seen = set()
    for m in messages:
        if set(m) - PUBLIC_FIELDS:
            raise ValueError('Document input contains undeclared or private fields')
        if m['source_id'] in seen:
            raise ValueError('Duplicate source ID in frozen input')
        seen.add(m['source_id'])
        collected = datetime.fromisoformat(m['collected_at'])
        written = datetime.fromisoformat(m['written_at'])
        if collected.utcoffset() is None or written.utcoffset() is None or written > collected:
            raise ValueError('Source timestamps must be timezone-aware and collected after writing')
        day=collected.astimezone(zone).date().isoformat()
        groups.setdefault(day,[]).append(m)
    return [(day,sorted(batch,key=lambda m:(datetime.fromisoformat(m['written_at']),m['source_id'])))
            for day,batch in sorted(groups.items())]


def document_text(batch, collected_at, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unknown document format version')
    if version in (LEGACY_VERSION,NIGHTLY_JSON_VERSION):
        return '\n'.join(json.dumps(m,ensure_ascii=False,sort_keys=True) for m in batch)+'\n'
    # A practical message export: copy every body, keep source chronology and
    # introduce each conversation once per contiguous block. Full machine
    # records, including source IDs, remain in messages.json for native readers
    # and integrity checks; they need not inflate the LLM's transcript.
    lines=[f'Daily messages — collected through {collected_at}']
    previous=None
    for m in batch:
        metadata=m.get('structured') or {}
        conversation=(m['channel'],m['thread'],metadata)
        if conversation!=previous:
            lines.extend(['',f"Conversation: {m['channel']} | {m['thread']}"])
            if metadata.get('chat_name'):lines.append('Name: '+metadata['chat_name'])
            if metadata.get('participants'):lines.append('Participants: '+', '.join(metadata['participants']))
            if metadata.get('email_subject'):lines.append('Subject: '+metadata['email_subject'])
            extra={k:v for k,v in metadata.items() if k not in ('chat_name','participants','email_subject')}
            if extra:lines.append('Metadata: '+json.dumps(extra,ensure_ascii=False,sort_keys=True))
            previous=conversation
        lines.extend(['',f"[{m['written_at']}] {m['sender']}"+(' (sent by owner)' if m['from_owner'] else '')])
        for key,label in (('arrived_at','Delivered'),('collected_at','Captured')):
            if m.get(key) and m[key]!=m['written_at']:lines.append(f'{label}: {m[key]}')
        lines.append(m['text'])
    return '\n'.join(lines)+'\n'


def prepare_sources(output, messages, timezone):
    from .imessage_fixture import append_messages
    from .email_fixture import append_emails
    batches=daily_batches(messages,timezone)
    schedule=[]
    previous=None
    previous_mail=None
    for day,batch in batches:
        at=max((m['collected_at'] for m in batch),key=datetime.fromisoformat)
        root=output/'sources'/day
        root.mkdir(parents=True)
        text=document_text(batch,at)
        (root/'messages.txt').write_text(text)
        save(root/'messages.json',batch)
        fixture=None
        imessages=[m for m in batch if m['channel']=='imessage']
        names=sorted({name for m in batch for name in (m.get('structured') or {}).get('participants',[])})
        if imessages or any(m['channel']=='email' for m in batch):
            if previous:
                for src in previous.glob('chat.*'):
                    shutil.copyfile(src,root/src.name)
            fixture=append_messages(root/'chat.db',imessages,contacts=names)
            previous=root
        elif previous:
            for src in previous.glob('chat.*'):
                shutil.copyfile(src,root/src.name)
        emails=[m for m in batch if m['channel']=='email']
        mailbox=None
        if emails:
            if previous_mail:
                shutil.copyfile(previous_mail/'mailbox.json',root/'mailbox.json')
            mailbox=append_emails(root/'mailbox.json',emails)
            previous_mail=root
        # Freeze the actual source format and availability separately from grading.
        save(root/'source.json',{'version':VERSION,'at':at,'source_ids':[m['source_id'] for m in batch],
            'document_sha256':hashlib.sha256(text.encode()).hexdigest(),'fixture':fixture,
            **({'mailbox':mailbox} if mailbox else {}),
            'fixture_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [*root.glob('chat.*'),*root.glob('mailbox.json')]}})
        schedule.append({'id':day,'at':at,'source_ids':[m['source_id'] for m in batch],
                         'source_file':f'sources/{day}/messages.txt','file':f'ingestion/{day}.json',
                         'source_manifest_sha256':hashlib.sha256((root/'source.json').read_bytes()).hexdigest()})
    return schedule


def processing_at(at, provider, timezone, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unknown ingestion clock version')
    if version==LEGACY_VERSION and provider!='memcal':
        return at
    stamp=datetime.fromisoformat(at).astimezone(ZoneInfo(timezone))
    return datetime.combine(stamp.date()+timedelta(days=1),datetime.min.time(),ZoneInfo(timezone)).replace(hour=3).isoformat()


def ready_sources(schedule, provider, at, timezone, *, version=VERSION):
    clock=datetime.fromisoformat(at)
    ready=[]
    for row in schedule:
        source_time=datetime.fromisoformat(row['at'])
        finished=datetime.fromisoformat(processing_at(row['at'],provider,timezone,version=version))
        if source_time<=clock<finished:
            raise ValueError('Evaluation precedes native processing; do not use future memory state')
        if finished<=clock:ready.append(row)
    return ready


def validate_evaluation_clocks(schedule,providers,question_times,timezone, *, chronological=False,version=VERSION):
    """Only completed daily batches may precede a checkpoint; never import its future."""
    questions=[datetime.fromisoformat(value) for value in question_times]
    if not questions or any(value.utcoffset() is None for value in questions):
        raise ValueError('Evaluation clocks require explicit timezone offsets')
    for provider in providers:
        for at in question_times:
            ready=ready_sources(schedule,provider,at,timezone,version=version)
            if not chronological and len(ready)!=len(schedule):
                raise ValueError('Evaluation precedes source availability; chronological checkpoint ingestion is required')
        if len(ready_sources(schedule,provider,max(questions).isoformat(),timezone,version=version))!=len(schedule):
            raise ValueError('Final evaluation precedes source availability or native processing')


def ingest_day(provider, source, root, env, at, timezone, *, timeout_s=None):
    """Provider-owned ingestion/processing in an isolated process, no Hermes turn."""
    # Honcho's asynchronous deriver needs a longer bounded batch barrier.
    if timeout_s is None:
        timeout_s=PROCESSING_TIMEOUT_S  # One nightly batch may contain several real conversations.
        if provider=='honcho':
            defaults=json.loads((root/'native-processing-settings.json').read_text())
            timeout_s=1020+defaults['deriver']['REPRESENTATION_BATCH_MAX_AGE_SECONDS']
    native_at=processing_at(at,provider,timezone)
    child_env=harness.turn_env(native_at,root/'hermes-home','',env)
    roles=json.loads(env['PACBENCH_USAGE_ROLES'])
    before=snapshot(roles)
    started=time.monotonic()
    record={'version':VERSION,'provider':provider,'at':at,'processing_at':native_at,
            'document_sha256':hashlib.sha256((source/'messages.txt').read_bytes()).hexdigest(),
            'source_ids':json.loads((source/'source.json').read_text())['source_ids'],
            'delivery':('native-sources' if provider=='memcal' else 'native-messages' if provider=='honcho' else 'document'),
            'hermes_ingestion_turns':0,'state':'failed','ready':False}
    try:
        proc=subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',WORKER,
                            str(harness.REPO),provider,str(source),str(root),at,native_at],
                           cwd=harness.ISOLATED_TREE,env=child_env,capture_output=True,text=True,timeout=timeout_s)
        record.update(returncode=proc.returncode,stdout=proc.stdout,stderr=proc.stderr)
        if proc.stdout.strip():
            try:
                result=json.loads(proc.stdout.splitlines()[-1])
            except (ValueError,IndexError):
                result=None
            if isinstance(result,dict):
                record.update(result=result,ready=proc.returncode==0 and result.get('ready') is True)
                remove_duplicate_stdout(record)
            record['state']='ready' if record['ready'] else 'failed'
    except subprocess.TimeoutExpired as exc:
        record.update(reason='Native ingestion/processing deadline exceeded',stdout=(exc.stdout or b'').decode() if isinstance(exc.stdout,bytes) else exc.stdout,
                      stderr=(exc.stderr or b'').decode() if isinstance(exc.stderr,bytes) else exc.stderr)
    except (OSError,ValueError,IndexError) as exc:
        record['reason']=f'{type(exc).__name__}: {exc}'
    finally:
        record['latency_s']=round(time.monotonic()-started,2)
        record['usage']=window(before,snapshot(roles))
        # Native exception messages must never publish an authentication token.
        token=env.get('OPENAI_API_KEY','')
        if token:
            for key in ('stdout','stderr','reason'):
                if isinstance(record.get(key),str):record[key]=record[key].replace(token,'<gateway-token>')
    return record


def record_recall_isolation(root):
    """Fail closed before recall if any Hermes conversation or builtin facts exist."""
    home=root/'hermes-home'
    sessions=[]
    if (home/'state.db').exists():
        with sqlite3.connect(f'file:{home/"state.db"}?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='sessions'").fetchone():
                sessions=[r[0] for r in db.execute('SELECT id FROM sessions')]
    texts={p.name:p.read_text() for p in (home/'memories').glob('*.md')}
    record={'hermes_sessions_before_recall':sessions,'builtin_memory_before_recall':texts}
    save(root/'pre-recall-isolation.json',record)
    if sessions or any(v.strip() for v in texts.values()):
        raise RuntimeError('Recall must start without ingestion conversations or builtin facts')
