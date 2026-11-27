"""Submit public exports through the documented native call. Acceptance is not readiness.

Clients are already provisioned, account-bound upstream clients. This module does not
install software, start services, change provider configuration, or resubmit writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from pacbench import harness
from pacbench.evidence_store import save
from pacbench.providers import eligibility
from pacbench.usage import snapshot, window
from pacbench.workflows import source_plan as source_plan


def submit_document(provider, client, account_scope, document):
    if (not document.filename.endswith('.txt') or Path(document.filename).name != document.filename
            or '\\' in document.filename):
        raise ValueError('expected a single public export filename')
    route = eligibility(provider, 'nightly-document')
    if not route['documented_for_scope']:
        raise ValueError(route['boundary'])
    method = route['routes'][0]['method']
    if method == 'Memory.add':
        return client.add(document.text, user_id=account_scope, infer=True,
                          metadata={'filename': document.filename, 'collected_through': document.cutoff})
    if method == 'remember_permanent':
        return client.remember_permanent(text=document.text, dataset=account_scope,
                                         session_ids=[], timeout=1800)
    if method == 'add':
        return client.add(account_scope, content=document.text, id=document.filename,
                          date=document.cutoff)
    if method == 'auto_resource':
        workspace = Path(account_scope)
        directory = workspace / 'resource' / datetime.fromisoformat(document.cutoff).date().isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / document.filename
        # Publish only complete bytes, and never replace a previous import.
        with tempfile.NamedTemporaryFile(dir=workspace, delete=False) as staged:
            temporary = Path(staged.name)
            try:
                staged.write(document.text.encode())
                staged.flush()
                os.link(temporary, destination)
            finally:
                temporary.unlink()
        return {'resource': str(destination)}
    with tempfile.TemporaryDirectory(prefix='pacbench-public-upload-') as staging:
        path = Path(staging) / document.filename
        path.write_bytes(document.text.encode())
        if method == 'retain_files':
            return client.retain_files(bank_id=account_scope, files=[path],
                                       files_metadata=[{'document_id': document.filename}])
        if method == 'resources':
            upload = client.upload_temp_file(path)
            return client.post('/api/v1/resources',
                               {'temp_file_id': upload, 'source_name': document.filename, 'wait': True},
                               timeout=180)
    raise ValueError(f'unsupported native method {method}')


class MemCalReaders:
    """Read an already provisioned synthetic Mac Messages store / IMAP mailbox.

    The synthetic upstream controls visibility at its own clock. This route does
    not copy a fabricated Messages database or inject text into the archive.
    """
    def __init__(self, connection, config, *, site=None):
        self.connection, self.config = connection, config
        self.site = site
        self.qualified_channels = None

    def preflight(self, account, channels):
        from pacbench.providers.memcal.site import inspect_memcal_readers

        self.qualified_channels = None
        if self.site is None or self.site.account != account:
            raise ValueError('A bound synthetic MemCal site matching the workflow account is required')
        report = inspect_memcal_readers(self.site, self.connection, self.config, channels=channels)
        self.qualified_channels = tuple(channels)
        return report

    def collect(self, channels):
        if tuple(channels) != self.qualified_channels:
            raise ValueError('MemCal mixed reader preflight is required before native collection')
        from pacbench.providers.memcal.site import _bound_sources
        readers, _ = _bound_sources(self.site, self.connection, self.config)
        from memcal import archive, identity, sources

        conn, cfg = self.connection, self.config
        identity.import_contacts(conn)
        collection = archive.open_collection(conn, mode='cli')
        failure = None
        reports = {}
        try:
            for channel in channels:
                report = sources.catch_up(readers[channel],conn,cfg,collection_id=collection)
                reports[channel] = {'read':report.read,'archived':report.archived,
                                    'more':report.more,'error':bool(report.error)}
                if report.error or report.more:
                    raise RuntimeError('native source reader incomplete')
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            archive.close_collection(conn,collection,error=failure)
        return {'collection_id':collection,'readers':reports}

    def process(self):
        from memcal import brief, db
        from memcal.dream.run import dream

        result = dream(self.connection, self.config)
        if result.errors:
            raise RuntimeError('native nightly processing incomplete')
        # A non-error return can still leave queued work. Verify the committed
        # outcome from a second connection, including rows visible through WAL.
        with closing(db.connect(self.config.db_path, read_only=True)) as reader:
            proof = completed_dream(reader, result.run_id)
        brief.write(self.connection,self.config)
        return proof


def completed_dream(reader, run_id):
    row = reader.execute(
        "SELECT finished_at, error, mode FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    pending = reader.execute("SELECT count(*) FROM spool WHERE processed_at IS NULL").fetchone()[0]
    if row is None or not row[0] or row[1] or row[2] != 'nightly' or pending:
        raise RuntimeError('native nightly Dream did not finish cleanly with an empty spool')
    return {'run_id': run_id, 'finished_at': row[0], 'pending_spool': pending}


PROCESSING_TIMEOUT_S = 900


HINDSIGHT_PROCESSING_TIMEOUT_S = 900


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
    # JSON strings may legally contain U+2028/U+2029; only LF frames CLI output.
    lines=(record.get('stdout') or '').split('\n')
    last=next((i for i in range(len(lines)-1,-1,-1) if lines[i].strip()),None)
    if last is None or not isinstance(record.get('result'),dict):return False
    try:same=json.loads(lines[last])==record['result']
    except (ValueError,TypeError):return False
    if not same:return False
    record['stdout']='\n'.join(lines[:last]+lines[last+1:])
    record['stdout_json_in']='result'
    return True


def prepare_sources(output, messages, timezone, *, native_fixtures=True, membership_condition=None,
                    observed_email_headers_only=False):
    from pacbench.email_fixture import append_emails
    from pacbench.imessage_fixture import append_messages
    schedule=[]
    previous=None
    previous_mail=None
    for day, at, batch in source_plan.daily_releases(messages, timezone):
        root=output/'sources'/day
        root.mkdir(parents=True)
        text=source_plan.document_text(batch,at)
        (root/'messages.txt').write_text(text)
        save(root/'messages.json',batch)
        fixture=None
        imessages=[m for m in batch if m['channel']=='imessage']
        names=sorted({name for m in batch for name in (m.get('structured') or {}).get('participants',[])})
        if native_fixtures and (imessages or any(m['channel']=='email' for m in batch)):
            if previous:
                for src in previous.glob('chat.*'):
                    shutil.copyfile(src,root/src.name)
            fixture=append_messages(root/'chat.db',imessages,contacts=names,
                membership_condition=membership_condition or os.environ.get('PACBENCH_IMESSAGE_MEMBERSHIP_CONDITION') or None)
            previous=root
        elif previous:
            for src in previous.glob('chat.*'):
                shutil.copyfile(src,root/src.name)
        emails=[m for m in batch if m['channel']=='email']
        mailbox=None
        if native_fixtures and (emails or observed_email_headers_only):
            if previous_mail:
                shutil.copyfile(previous_mail/'mailbox.json',root/'mailbox.json')
            mailbox=append_emails(root/'mailbox.json',emails,observed_headers_only=observed_email_headers_only)
            previous_mail=root
        # Freeze the actual source format and availability separately from grading.
        save(root/'source.json',{'version':source_plan.VERSION,'at':at,'source_ids':[m['source_id'] for m in batch],
            'document_sha256':hashlib.sha256(text.encode()).hexdigest(),'fixture':fixture,
            **({'transport_condition':'logical-document-metadata-v1'} if not native_fixtures else {}),
            **({'mailbox':mailbox} if mailbox else {}),
            'fixture_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [*root.glob('chat.*'),*root.glob('mailbox.json')]}})
        schedule.append({'id':day,'at':at,'source_ids':[m['source_id'] for m in batch],
                         'source_file':f'sources/{day}/messages.txt','file':f'ingestion/{day}.json',
                         'source_manifest_sha256':hashlib.sha256((root/'source.json').read_bytes()).hexdigest()})
    return schedule


def ingest_day(provider, source, root, env, at, timezone, *, timeout_s=None):
    """Provider-owned ingestion/processing in an isolated process, no Hermes turn."""
    from pacbench.process_cleanup import OperatorStop, check_stop
    check_stop(env.get('PACBENCH_STOP_FILE'))
    # Honcho's asynchronous deriver needs a longer bounded batch barrier.
    if timeout_s is None:
        timeout_s=PROCESSING_TIMEOUT_S  # One nightly batch may contain several real conversations.
        if provider=='cognee':
            timeout_s=1830  # Cognify and native automatic improve must both finish.
        if provider=='hindsight':
            # Retain plus native consolidation can outlast the generic batch
            # watchdog during upstream retries. Completion remains mandatory;
            # give the connector time to save its terminal readiness evidence.
            timeout_s=HINDSIGHT_PROCESSING_TIMEOUT_S+30
        if provider=='honcho':
            defaults=json.loads((root/'native-processing-settings.json').read_text())
            timeout_s=1020+defaults['deriver']['REPRESENTATION_BATCH_MAX_AGE_SECONDS']
    native_at=source_plan.processing_at(at,provider,timezone)
    child_env=harness.turn_env(native_at,root/'hermes-home','',env)
    roles=json.loads(env['PACBENCH_USAGE_ROLES'])
    before=snapshot(roles)
    started=time.monotonic()
    record={'version':source_plan.VERSION,'provider':provider,'at':at,'processing_at':native_at,
            'processing_timeout_s':timeout_s,
            'document_sha256':hashlib.sha256((source/'messages.txt').read_bytes()).hexdigest(),
            'source_ids':json.loads((source/'source.json').read_text())['source_ids'],
            'delivery':('native-sources' if provider=='memcal' else 'native-messages' if provider=='honcho' else 'document'),
            'hermes_ingestion_turns':0,'state':'failed','ready':False}
    try:
        if provider == 'honcho':
            from provider_adapters.honcho_enrollment import ENVIRONMENT, PROTOCOL
            if env.get(ENVIRONMENT):
                if env[ENVIRONMENT] != PROTOCOL:
                    raise ValueError('Unsupported Honcho membership protocol')
                # Real-clock controller; only native DB/API/deriver use the
                # provider calendar. Enroll before advancing to overnight import.
                enrollment = subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),
                    '-m','provider_adapters.honcho_enrollment',str(source),str(root)],
                    cwd=harness.REPO,env=child_env,capture_output=True,text=True,timeout=180)
                result=json.loads(enrollment.stdout.rstrip('\r\n').rsplit('\n',1)[-1])
                record['membership_protocol']=PROTOCOL
                record['enrollment']=result
                if enrollment.returncode == 75 and result.get('operator_stop') is True:
                    raise OperatorStop('operator stop requested during native enrollment')
                if enrollment.returncode or result.get('ready') is not True:
                    record.update(reason='Native chronological membership could not be established',
                                  native_error=result.get('native_error',{}))
                    return record
            from provider_adapters.honcho_clock import before_turn, enabled
            if enabled(root):
                before_turn(root, native_at, env)
        proc=subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',WORKER,
                            str(harness.REPO),provider,str(source),str(root),at,native_at],
                           cwd=harness.ISOLATED_TREE,env=child_env,capture_output=True,text=True,timeout=timeout_s)
        record.update(returncode=proc.returncode,stdout=proc.stdout,stderr=proc.stderr)
        if proc.stdout.strip():
            try:
                result=json.loads(proc.stdout.rstrip('\r\n').rsplit('\n',1)[-1])
            except (ValueError,IndexError):
                result=None
            if proc.returncode == 75 and isinstance(result, dict) and result.get('operator_stop') is True:
                raise OperatorStop('operator stop requested before native ingestion')
            if isinstance(result,dict):
                record.update(result=result,ready=proc.returncode==0 and result.get('ready') is True)
                remove_duplicate_stdout(record)
            record['state']='ready' if record['ready'] else 'failed'
    except subprocess.TimeoutExpired as exc:
        record.update(reason='Native ingestion/processing deadline exceeded',stdout=(exc.stdout or b'').decode() if isinstance(exc.stdout,bytes) else exc.stdout,
                      stderr=(exc.stderr or b'').decode() if isinstance(exc.stderr,bytes) else exc.stderr)
    except (OSError,ValueError,IndexError) as exc:
        if provider in ('honcho','supermemory'):
            from provider_adapters.document_connector import native_error_receipt
            record.update(reason='Native worker failed; raw error withheld',native_error=native_error_receipt(exc))
        else:record['reason']=f'{type(exc).__name__}: {exc}'
    finally:
        record['latency_s']=round(time.monotonic()-started,2)
        record['usage']=window(before,snapshot(roles))
        if provider=='honcho':
            journal=root/'ingestion-delivery'/source.name/'index.json'
            if journal.exists():
                raw=journal.read_bytes()
                record['delivery_journal']={'path':str(journal.relative_to(root)),
                                          'sha256':hashlib.sha256(raw).hexdigest()}
                from provider_adapters.document_connector import verify_honcho_delivery
                try:verify_honcho_delivery(journal,complete=record['ready'])
                except (ValueError,OSError,KeyError,TypeError):
                    record.update(ready=False,state='failed',reason='Native delivery receipt integrity failed')
        if provider in ('honcho','supermemory'):
            # Keep parsed acknowledgments, not an SDK traceback that may echo
            # the complete input or credentials. This also covers timeouts.
            streams={}
            for name in ('stdout','stderr'):
                value=record.get(name) or ''
                if value:
                    raw=value.encode()
                    streams[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
                record[name]=''
            if streams:record['worker_diagnostics']=streams
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
