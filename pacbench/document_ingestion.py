"""Lossless daily source delivery, without Hermes ingestion or fact extraction."""
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

VERSION = 'daily-source-documents-v1'
PUBLIC_FIELDS = {'source_id','channel','thread','sender','text','from_owner',
                 'written_at','arrived_at','collected_at','structured'}
BLOCKERS = {
    'holographic':'Unsupported: fact storage and regex extraction do not provide document ingestion.',
    'honcho':'Unsupported on verified evidence: peer/session message modeling, no documented document import.',
    'memori':'Unsupported on verified evidence: completed-agent-turn capture, no verified document import.',
    'retaindb':'Document API verified, but hosted account/cost boundary unavailable; documented $20/month subscription exceeds task budget.',
    'byterover':'Document/CLI curation candidate; raw-file import and gateway-only inference not verified yet.',
    'supermemory':'Document API verified; run-owned local server and inference spending boundary not yet provisioned.',
}
WORKER = '''
import sys
sys.path.insert(0, sys.argv[1])
from provider_adapters.document_connector import main
raise SystemExit(main(sys.argv[2:]))
'''


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


def document_text(batch, collected_at):
    # JSON lines are text, with unambiguous escaping and all original public fields.
    # No narrative instructions, synthetic assistant responses or fact extraction.
    return '\n'.join(json.dumps(m,ensure_ascii=False,sort_keys=True) for m in batch)+'\n'


def prepare_sources(output, messages, timezone):
    from .imessage_fixture import append_messages
    batches=daily_batches(messages,timezone)
    schedule=[]
    previous=None
    for day,batch in batches:
        at=max((m['collected_at'] for m in batch),key=datetime.fromisoformat)
        root=output/'sources'/day
        root.mkdir(parents=True)
        text=document_text(batch,at)
        (root/'messages.txt').write_text(text)
        save(root/'messages.json',batch)
        fixture=None
        if all(m['channel']=='imessage' for m in batch):
            if previous:
                for src in previous.glob('chat.*'):
                    shutil.copyfile(src,root/src.name)
            fixture=append_messages(root/'chat.db',batch)
            previous=root
        # Freeze the actual source format and availability separately from grading.
        save(root/'source.json',{'version':VERSION,'at':at,'source_ids':[m['source_id'] for m in batch],
            'document_sha256':hashlib.sha256(text.encode()).hexdigest(),'fixture':fixture,
            'fixture_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('chat.*')}})
        schedule.append({'id':day,'at':at,'source_ids':[m['source_id'] for m in batch],
                         'source_file':f'sources/{day}/messages.txt','file':f'ingestion/{day}.json',
                         'source_manifest_sha256':hashlib.sha256((root/'source.json').read_bytes()).hexdigest()})
    return schedule


def processing_at(at, provider, timezone):
    if provider!='memcal':
        return at
    stamp=datetime.fromisoformat(at).astimezone(ZoneInfo(timezone))
    return datetime.combine(stamp.date()+timedelta(days=1),datetime.min.time(),ZoneInfo(timezone)).replace(hour=3).isoformat()


def ingest_day(provider, source, root, env, at, timezone, *, timeout_s=240):
    """Provider-owned ingestion/processing in an isolated process, no Hermes turn."""
    native_at=processing_at(at,provider,timezone)
    child_env=harness.turn_env(native_at,root/'hermes-home','',env)
    roles=json.loads(env['PACBENCH_USAGE_ROLES'])
    before=snapshot(roles)
    started=time.monotonic()
    record={'version':VERSION,'provider':provider,'at':at,'processing_at':native_at,
            'document_sha256':hashlib.sha256((source/'messages.txt').read_bytes()).hexdigest(),
            'source_ids':json.loads((source/'source.json').read_text())['source_ids'],
            'delivery':'native-imessage' if provider=='memcal' else 'document',
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
