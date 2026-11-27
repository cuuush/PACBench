"""Continuing-account OpenViking native file and conversation witnesses."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import time
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pacbench.evidence_store import save
from pacbench.gateway.observer import PROTOCOL
from pacbench.providers import HERMES_TOOLS, validate_native_deployment
from pacbench.workflows.native import receipt_lineage
from pacbench.workflows.progress import process_identity


def uri_rows(client, root):
    """Native recursive listing, excluding generated hidden summaries."""
    page = client.get('/api/v1/fs/ls', params={'uri':root,'recursive':True,'output':'original',
                                              'include_abstract':False,'node_limit':1000},timeout=20)
    if page.get('status')!='ok' or page.get('has_more') or not isinstance(page.get('result'),list):
        raise RuntimeError('OpenViking native resource tree is incomplete')
    rows = {r['uri']:r for r in page['result']}
    if len(rows)!=len(page['result']):
        raise RuntimeError('OpenViking reported repeated resource identities')
    return rows


def observe_queues(client, *, deadline):

    while time.monotonic()<deadline:
        value = client.get('/api/v1/observer/queue?format=json',timeout=15)
        state = openviking_observer_state(value)
        if state=='ready':
            return value
        if state!='busy':
            raise RuntimeError('OpenViking native queues reported errors')
        time.sleep(.25)
    raise TimeoutError('OpenViking native queues did not become processing-ready')


class OpenVikingWitness:
    def __init__(self, root, owner, client, server, documents, all_documents, ledger):
        self.root,self.owner,self.client,self.server,self.ledger = root,owner,client,server,ledger
        self.documents = tuple(documents)
        self.cutoffs = list(dict.fromkeys(d.cutoff for d in documents))
        self.planned_names = {PurePosixPath(doc.filename).stem for doc in all_documents}
        self.initial_uris = set()
        self.sources = {}
        self.captures = {}
        self.explicit_writes = []
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        from ..catalog_plugins import verify_retained_catalog_provider

        verify_retained_catalog_provider('openviking', self.owner.home)
        if self.owner.provider!='openviking' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('OpenViking gateway or run-owned server identity changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.root/'openviking-data'),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='0.5.4.1',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['openviking']),
            native_processes={'memory':self.server,'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def control_readback(self):
        native = uri_rows(self.client,'viking://resources')
        sessions = self.client.get('/api/v1/sessions').get('result')
        if native or sessions!=[]:
            raise RuntimeError('OpenViking native resources or sessions already contain records')
        self.initial_uris = set(native)
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_resource_count':len(native),'native_session_count':len(sessions)})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('OpenViking source releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        if doc.cutoff!=self.cutoff or raw.get('status')!='ok' or not isinstance(raw.get('result'),dict):
            raise RuntimeError('OpenViking native upload acceptance is missing')
        result = raw['result']
        queues = result.get('queue_status')
        uri = result.get('root_uri','')
        if (result.get('status')!='success' or result.get('errors') or not uri.startswith('viking://resources/')
                or not queues or any(q.get('errors') or q.get('error_count') for q in queues.values())):
            raise RuntimeError('OpenViking file processing did not return a valid native resource')
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'temp_upload and resources(wait=true)','root_uri':uri})

    def document_readback(self, doc, acceptance):

        if self.documents[len(self.sources)]!=doc:
            raise ValueError('OpenViking file readback differs from frozen source order')
        deadline = time.monotonic()+180
        waited = self.client.post('/api/v1/system/wait',{},timeout=150)
        queues = waited.get('result')
        if (waited.get('status')!='ok' or not isinstance(queues,dict) or not queues
                or any(q.get('error_count')!=0 or q.get('errors')!=[] for q in queues.values())):
            raise RuntimeError('OpenViking global processing wait failed')
        queue = observe_queues(self.client,deadline=deadline)
        uri = acceptance['request']['root_uri']
        tree = uri_rows(self.client,'viking://resources')
        leaves = [key for key,val in tree.items() if (key==uri or key.startswith(uri+'/'))
                  and not val.get('isDir',val.get('is_dir',False))]
        proof = []
        for key in leaves:
            raw = self.client.get('/api/v1/content/read',params={'uri':key,'raw':True},timeout=15).get('result')
            if isinstance(raw,str) and hashlib.sha256(raw.encode()).hexdigest()==doc.sha256:
                proof.append(key)
        responses = [r for r in self.ledger.snapshot() if r.get('workflow_slot',{}).get('batch_id')==doc.filename]
        generated = openviking_generated_artifacts(self.root/'openviking-data')
        failures = openviking_failure_log(self.root/'openviking-server.log')
        native_directory = PurePosixPath('viking')/self.client._account/uri.removeprefix('viking://')
        scoped = [row for row in generated['files'] if PurePosixPath(row['path']).is_relative_to(native_directory)]
        if (len(proof)!=1 or generated['state']!='complete' or failures['state']!='complete'
                or not scoped or any(item['failed_placeholder'] for item in scoped)
                or openviking_response_evidence(responses)['state']!='complete'):
            raise RuntimeError('OpenViking raw source, generated content or processed output is unproven')
        self.sources[doc.filename] = (uri,proof[0])
        return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=doc.sha256,
                    readback=dict(uri=proof[0],root_uri=uri,queue=queue,model_attempts=[r['id'] for r in responses],
                                  generated_source_artifacts=scoped))

    def source_inventory(self):
        rows = uri_rows(self.client,'viking://resources')
        visible = {uri for pair in self.sources.values() for uri in pair}
        if not visible.issubset(rows):
            raise RuntimeError('OpenViking released native resource is missing')
        # A future benchmark filename cannot hide under a different parent.
        issued = {PurePosixPath(name).stem for name in self.sources}
        for uri in rows:
            identities = {PurePosixPath(part).stem for part in uri.split('/')}
            if (identities & self.planned_names)-issued:
                raise RuntimeError('OpenViking inventory contains an unreleased benchmark source')
        return list(self.sources)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic()+90
        expected = self.owner.witness
        identity = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('OpenViking native lifecycle observer failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for row in sample['sessions'] for p in row['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=identity or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256']
                        or sample['native_failures'] or any(p['name']!='openviking'
                           or p.get('endpoint')!=self.client._endpoint for p in providers)):
                    raise RuntimeError('OpenViking lifecycle observation does not bind the native service')
                active = (sample['native_release_threads'] or any(row['pending_futures'] for row in sample['sessions'])
                          or any(p['active_writers'] for p in providers))
                if not active and sample['observed_monotonic_ns']>=since:
                    queue = observe_queues(self.client,deadline=deadline)
                    pending = any(not row.get('finished') and row['accounting_role']!='judge' for row in self.ledger.snapshot())
                    if not pending:
                        return dict(gateway_identity=identity,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                    readback={'gateway':sample,'queue':queue})
            time.sleep(.05)
        raise RuntimeError('OpenViking native open-account work did not finish')

    def observe_capture(self, captures):
        from urllib.parse import quote

        from pacbench.native_writes import capture_snapshot

        return capture_snapshot(self.root,dict(turns=captures,explicit_writes=self.explicit_writes),
                                lambda id:self.client.get('/api/v1/tasks/'+quote(id,safe=''),timeout=15)['result'],
                                account=self.client._account,user=self.client._user)

    def native_write_barrier(self, session, answer):
        from pacbench.evidence_store import binding_digest as digest

        self.idle()
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (session,)).fetchone()
        scoped = self.captures.setdefault(session,[])
        capture = dict(session=session,prompt=prompt,answer=answer,turn=len(scoped))
        capture['binding'] = digest(capture)
        scoped.append(capture)
        result = self.observe_capture(scoped)
        if result['state']=='native_failed' or not all(row['both_sides_captured'] for row in result['captures']):
            raise RuntimeError('OpenViking native session lacks its completed conversation capture')
        return dict(session_id=session,pending_writes=0,readback=result['captures'][-1])

    def after_native_close(self, session):
        deadline = time.monotonic()+120
        path = self.owner.home/'state.db'
        while time.monotonic()<deadline:
            sample = self.idle()['readback']['gateway']
            managers = [r for r in sample['sessions'] if r['session_id']==session]
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                end = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(session,)).fetchone()
            if end and end[0] is not None and end[1]=='cli_close' and managers and all(
                    r['shutdown_returned'] and r['clean'] for r in managers):
                committed = self.observe_capture(self.captures[session])
                if committed['state']=='complete':
                    return dict(session_id=session,pending_writes=0,readback=committed)
                if committed['state']=='native_failed':
                    raise RuntimeError('OpenViking native session commit failed')
            time.sleep(.25)
        raise RuntimeError('OpenViking session closure or native commit readiness was not proven')

    def tool_boundaries(self, results):
        from urllib.parse import quote

        boundaries = {}
        for row in results:
            if row['tool']!='viking_remember' or not row['success']:
                continue
            value = json.loads(row['text'])
            task_id = value.get('task_id')
            if not isinstance(task_id,str) or not task_id or any(w['task_id']==task_id for w in self.explicit_writes):
                raise RuntimeError('OpenViking explicit native write lacks a distinct task ID')
            witness = {'task_id':task_id,'session':value.get('session_id')}
            deadline = time.monotonic()+120
            while time.monotonic()<deadline:
                proof = self.client.get('/api/v1/tasks/'+quote(task_id,safe=''),timeout=15)['result']
                if (proof.get('task_id')!=task_id or proof.get('task_type')!='session_commit'
                        or witness['session'] and proof.get('resource_id')!=witness['session']
                        or proof.get('error') or proof.get('status') in ('failed','cancelled')):
                    raise RuntimeError('OpenViking explicit write native completion failed')
                if proof['status']=='completed':
                    returned = proof.get('result') or {}
                    if returned.get('errors') or returned.get('status') in ('failed','error'):
                        raise RuntimeError('OpenViking explicit write contains native errors')
                    self.explicit_writes.append(witness)
                    boundaries[row['tool_call_id']] = {'native_write_task':task_id,'native_status':'completed'}
                    break
                time.sleep(.25)
            else:
                raise TimeoutError('OpenViking explicit native write did not complete')
        return boundaries

    def request_lineage(self, requests):
        return receipt_lineage(self.ledger.snapshot(), requests)

    def close(self):
        pass


def check_canary(root):
    """Verify the retained native source, session, process and receipt evidence."""
    from pacbench.evidence_store import file_binding as binding
    from pacbench.judging.retained_scope import read_canary_binding as read
    from pacbench.judging.retained_week import (
        audit_document_canary,
        verify_listener_stopped,
    )
    from pacbench.workflows.progress import OwnershipConflict

    from ..catalog_plugins import verify_retained_catalog_provider

    audit = audit_document_canary(root, 'openviking')
    root = audit.root
    verify_retained_catalog_provider('openviking', root/'hermes-home')
    setup = read(binding(root/'native-server-setup.json'))
    server = audit.deployment['native_processes']['memory']
    closes = [row for row in audit.events if row['phase'] == 'chat-closed']
    if (audit.condition['version'] != '0.5.4.1'
            or tuple(audit.condition['tool_names']) != HERMES_TOOLS['openviking']
            or setup['version'] != '0.5.4.1' or setup['pid'] != server['pid']
            or read(binding(root/'import-condition.json'))['condition'] != 'native-defaults'
            or any(row['evidence']['readback']['state'] != 'complete' for row in closes)):
        raise ValueError('OpenViking canary lacks its native ingestion or shutdown proof')
    try:
        observed = process_identity(server['pid'])
    except (FileNotFoundError, OwnershipConflict):
        observed = None
    if observed == server:
        raise ValueError('OpenViking canary server remains active')
    verify_listener_stopped(setup['endpoint'], 'OpenViking')
    return audit.bindings


def verify_open_clone(observation, manifest):
    """Validate native startup housekeeping without ignoring resource changes."""
    from ..hermes_gateway import GatewayLifecycleError

    endpoint=observation['endpoint']
    if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',endpoint):
        raise GatewayLifecycleError('OpenViking startup proof requires a run-owned loopback endpoint')
    closed=observation['snapshot_manifest']['files']
    opened=manifest['files']
    runtime_files={'_system/queue/queue.db-shm','_system/queue/queue.db-wal',
                   '_system/usage_audit/usage_audit.sqlite3','_system/usage_audit/usage_audit.sqlite3-shm',
                   '_system/usage_audit/usage_audit.sqlite3-wal'}
    changed={path for path in closed.keys() | opened.keys() if closed.get(path)!=opened.get(path)}
    unexpected=sorted(path for path in changed if path not in runtime_files and not re.fullmatch(
            r'vectordb/context/store/(?:CURRENT|LOCK|LOG(?:\.old)?|MANIFEST-[0-9]+|[0-9]+\.(?:log|ldb))',
            path))
    if unexpected:
        raise GatewayLifecycleError('OpenViking startup changed non-runtime store files: '+', '.join(unexpected))
    reports={}
    for uri in observation['native_resource_uris']:
        request=urllib.request.Request(endpoint+'/api/v1/system/consistency',
            data=json.dumps({'uri':uri}).encode(),headers={'Content-Type':'application/json',
                'X-OpenViking-Account':'default','X-OpenViking-User':'default'})
        with urllib.request.urlopen(request,timeout=10) as response:
            report=json.load(response)
        data=report.get('result',{})
        if report.get('status')!='ok' or data.get('ok') is not True or not data.get('expected_count'):
            raise GatewayLifecycleError('OpenViking opened index failed native resource consistency')
        reports[uri]=data
    return {'changed_storage_files':sorted(changed),'index_reports':reports}


def read(path):
    return json.loads(path.read_text())


def renew_empty_workspace(root, history):
    """A temporary workspace belongs to its runner's process lifetime."""
    from pacbench.context_isolation import workspace_for
    from pacbench.workflows.delivery import record_recall_isolation
    record_recall_isolation(root)
    receipt=root/'recall-workspace.json'
    if receipt.exists():shutil.move(receipt,history/'recall-workspace.json')
    # Create this in the long-lived runner, so its atexit cleanup cannot run
    # before the actual Hermes questions. Never delete the old directory.
    return workspace_for(root/'hermes-home')


def ready_prefix(output, report, messages):
    from pacbench.workflows.source_plan import daily_batches, document_text
    root=output/'repeat-1/openviking'
    if list((root/'turns').glob('*/turn.json')) or (root/'recall.json').exists():
        raise ValueError('Ingestion resume cannot replay or replace existing answers')
    if (root/'evaluation.json').exists() and any(
            c.get('verdict') is not None for c in read(root/'evaluation.json').get('checks',[])):
        raise ValueError('Ingestion resume cannot replace existing grades')
    expected=daily_batches(messages,report['process_timezone'])
    schedule=report['ingestion_schedule']
    if [d for d,_ in expected]!=[r['id'] for r in schedule]:
        raise ValueError('Resume source schedule differs from frozen corpus')
    completed=[];missing=False
    for row,(_,batch) in zip(schedule,expected):
        source=output/'sources'/row['id']
        meta=read(source/'source.json')
        text=document_text(batch,row['at'],version=report['ingestion_version'])
        sha=hashlib.sha256(text.encode()).hexdigest()
        if (read(source/'messages.json')!=batch or (source/'messages.txt').read_text()!=text or
                row['source_ids']!=[m['source_id'] for m in batch] or meta['source_ids']!=row['source_ids'] or
                meta['at']!=row['at'] or
                meta['document_sha256']!=sha or
                hashlib.sha256((source/'source.json').read_bytes()).hexdigest()!=row['source_manifest_sha256']):
            raise ValueError('Resume source delivery changed')
        for name,expected_sha in meta.get('fixture_hashes',{}).items():
            if Path(name).name!=name or hashlib.sha256((source/name).read_bytes()).hexdigest()!=expected_sha:
                raise ValueError('Resume native source fixture changed')
        path=root/row['file']
        if not path.exists():missing=True;continue
        record=read(path)
        if (missing or record.get('ready') is not True or record.get('state')!='ready' or
                record.get('provider')!='openviking' or record.get('source_ids')!=row['source_ids'] or
                record.get('document_sha256')!=sha or record.get('at')!=row['at']):
            raise ValueError('Only a contiguous, successful import prefix can be resumed')
        native=record.get('result',{}).get('result',{}).get('result',{})
        if (native.get('root_uri')!='viking://resources/'+row['id'] or
                audit_openviking_receipt(record.get('result',{}))):
            raise ValueError('Missing healthy native persisted resource receipt')
        completed.append((row['id'],native['root_uri']))
    if not completed:raise ValueError('No successful imports to resume')
    return completed


def resume(output, *, max_messages=4500, question_dir=None):
    from pacbench.providers import require_live_trials_ready
    require_live_trials_ready()
    from pacbench.corpus.evaluation import judge_evidence, load_evaluations
    from pacbench.gateway_client import (
        client_settings,
        load_driver,
        require_live_cost_boundary,
        validate_route,
    )
    from pacbench.model_calls import ModelClient
    from pacbench.native_audit import audit
    from pacbench.providers.provision import (
        probe,
        provision,
        smoke_succeeded,
        stop_arm_model_work,
        verify_retained_trial_home,
    )
    from pacbench.source_policy import policy_record
    from pacbench.usage import snapshot
    from pacbench.workflows.delivery import record_recall_isolation
    from pacbench.workflows.native_run import read_prepared, run_checks
    from pacbench.workflows.source_plan import ready_sources
    output=output.resolve();report=read(output/'summary.json')
    from pacbench.judging.grade import (
        DIAGNOSTIC_VERSIONS,
        JUDGE_PROTOCOLS,
        LEGACY_DIAGNOSTIC_VERSION,
        LEGACY_JUDGE_PROTOCOL,
    )
    if report.get('provenance_version', 'source-provenance-v1') not in {'source-provenance-v1', 'source-provenance-v2', 'source-provenance-v3'}:
        raise ValueError('Unsupported provenance protocol')
    if 'evidence_diagnostic_protocol' in report and report['evidence_diagnostic_protocol'] not in DIAGNOSTIC_VERSIONS:
        raise ValueError('Unsupported evidence diagnostic protocol')
    if report.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) not in JUDGE_PROTOCOLS:
        raise ValueError('Unsupported semantic judge protocol')
    from pacbench.context_isolation import NATIVE_QUESTION as NATIVE
    from pacbench.harness import PROMPT_PROTOCOL
    if report.get('question_prompt_protocol') != PROMPT_PROTOCOL:
        raise ValueError('Resume requires the current question prompt protocol; legacy or unknown runs cannot change condition')
    if report.get('clock_context_protocol') != NATIVE:
        raise ValueError('Resume requires native-question-v2 clock context; historical instruction conditions are audit-only')
    if (report.get('state')!='incomplete' or len(report['results'])!=1 or
            report['results'][0]['provider']!='openviking' or report['results'][0]['repetition']!=1):
        raise ValueError('Resume requires one interrupted OpenViking arm')
    messages,contract,resolved=read_prepared((output/report['source']).resolve(),max_messages)
    source=(output/report['source']).resolve()
    if (hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=report['input_hash'] or
            contract.contract_hash!=report['contract_hash'] or
            read(source/'summary.json')['bundle_hash']!=report['bundle_hash'] or
            resolved['timezone']!=report['process_timezone']):
        raise ValueError('Resume frozen input identity or timezone changed')
    for name in ('bundle.json','contract.json','resolved-spec.json','evaluation-plan.json'):
        if (output/name).read_bytes()!=(source/name).read_bytes():
            raise ValueError('Resume frozen source/contract changed')
    completed=ready_prefix(output,report,messages);root=output/'repeat-1/openviking'
    verify_retained_trial_home('openviking', root/'hermes-home')
    record_recall_isolation(root)
    cfg=read(root/'ov.conf');_,key=client_settings('resume-validation')
    validate_route(cfg['vlm']['api_base'],cfg['vlm']['api_key'])
    if (Path(cfg['storage']['workspace']).resolve()!=root/'openviking-data' or
            not (root/'openviking-data').is_dir() or cfg['vlm'].get('max_tokens') is not None):
        raise ValueError('Resume requires the original native store and processing defaults')
    health=require_live_cost_boundary();driver=load_driver()
    if cfg['vlm']['model']!=driver.model_id:raise ValueError('Resume model changed')
    plan,checks=load_evaluations(output,messages,resolved)
    if plan['plan_hash']!=report['evaluation_plan_hash']:raise ValueError('Resume questions changed')
    if any(ready_sources(report['ingestion_schedule'],'openviking',row['at'],report['process_timezone'])
           !=report['ingestion_schedule'] for row,_,_ in checks):
        raise ValueError('Ingestion resume currently requires final-only questions')
    history=root/'resumes'/str(1+len(list((root/'resumes').glob('*'))))
    history.mkdir(parents=True)
    for name in ('summary.json','native-audit.json'):
        if (output/name).exists():shutil.copyfile(output/name,history/name)
    for name in ('evaluation.json','model-usage.json','model-usage-after-settlement.json','native-server-setup.json'):
        if (root/name).exists():shutil.copyfile(root/name,history/name)
    renew_empty_workspace(root,history)
    env=None
    try:
        env=provision(root,'openviking',driver.model_id,local_timezone=report['process_timezone'],resume=True)
        if report.get('clock_context_protocol'):
            env['PACBENCH_CLOCK_CONTEXT_PROTOCOL']=report['clock_context_protocol']
            from pacbench.context_isolation import NATIVE_QUESTION as CLOCK_CONTEXT
            if report['clock_context_protocol']==CLOCK_CONTEXT:env['PACBENCH_API_INPUT_TRACE']=str(root/'api-inputs.jsonl')
        restarted=read(root/'ov.conf')
        if any(restarted.get(k)!=cfg.get(k) for k in ('storage','embedding','vlm')):
            raise ValueError('Native processing configuration changed on restart')
        save(history/'resume.json',{'input_hash':report['input_hash'],
             'completed_days':[day for day,_ in completed],'gateway_before':health,
             'processing_configuration_preserved':True,'policy':'Successful imports only; no prior answers'})
        (root/'model-usage-after-settlement.json').unlink(missing_ok=True)
        # Confirm the native server reopened every retained resource before
        # skipping its import. This is ingestion telemetry, not Hermes recall.
        persisted=[]
        for day,uri in completed:
            url=env['OPENVIKING_ENDPOINT']+'/api/v1/fs/stat?'+urllib.parse.urlencode({'uri':uri})
            request=urllib.request.Request(url,headers={'X-OpenViking-Account':env['OPENVIKING_ACCOUNT'],
                                                       'X-OpenViking-User':env['OPENVIKING_USER']})
            with urllib.request.urlopen(request,timeout=10) as response:status=json.load(response)
            if status.get('status')!='ok' or not status.get('result'):
                raise RuntimeError('Native persisted resource unavailable: '+day)
            persisted.append({'day':day,'uri':uri,'stat':status})
        save(history/'persisted-resources.json',persisted)
        capability=probe(root,'openviking',env,smoke=True,smoke_timeout_s=120)
        if not smoke_succeeded(capability):raise RuntimeError('Resumed native capability check failed')
        policy=policy_record('openviking',capability['tools'])
        if policy!=read(root/'tool-policy.json'):raise ValueError('Resume tool policy changed')
        env['PACBENCH_SOURCE_ARGUMENT_RULES']=json.dumps(policy['source_argument_rules'])
        result=report['results'][0];result.update(state='available-unverified',capability=capability)
        from pacbench.native_writes import VERSION as WRITE_READINESS
        report['write_readiness_protocol']=result['write_readiness_protocol']=WRITE_READINESS
        capability['wiring_verified']=True
        report.pop('spending_stop',None);report['state']='running';save(output/'summary.json',report)
        from pacbench.process_cleanup import check_stop
        from pacbench.workflows.native_run import check_provider_health, ingest_pending

        ingested = [day for day, _ in completed]
        failed_batches = set()
        stop_file = output / 'STOP'
        def before_check(row, directory):
            check_stop(stop_file)
            check_provider_health(root, env)
            ingest_pending(report['ingestion_schedule'], ingested, failed_batches, output, root,
                           'openviking', env, report['process_timezone'], stop_file)
            if not (root/'pre-recall-isolation.json').exists():record_recall_isolation(root)
            from pacbench.native_writes import ensure_ready
            ensure_ready('openviking',row,checks,plan['context'],root,env,directory)
            from pacbench.workflows.source_plan import source_state
            save(directory/'source-state.json',source_state(row['at'],ingested))
        # No prior answers exist; preserve the earlier ungraded statuses.
        if (root/'turns').exists():shutil.move(root/'turns',history/'turns')
        (root/'evaluation.json').unlink(missing_ok=True)
        judge=ModelClient(key=key,model=driver.model_id,record_dir=output/'model-calls',
                          url=client_settings('pacbench-judge')[0]+'/chat/completions',
                          reasoning_effort=report.get('judge_generation_conditions',{}).get('reasoning_effort'))
        def evidence_for_check(row):
            return judge_evidence(output, messages, resolved, row, plan['context'], diagnostic_version=report.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION))
        sequence=run_checks(checks,plan['context'],root,judge,','.join(policy['allowed_tools']),env,
                            question_dir=question_dir or output/'combined-questions',before_check=before_check,
                            provenance_version=report.get('provenance_version','source-provenance-v1'),
                            evidence_by_id=evidence_for_check if report.get('evidence_diagnostic_protocol') else None,
                            diagnostic_version=report.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION),
                            judge_protocol=report.get('judge_protocol', LEGACY_JUDGE_PROTOCOL))
        result.update(state=sequence['state'],checks=sequence['checks'],
                      reason='' if sequence['state']=='diagnostic' else 'Resumed evaluation incomplete')
        capability['live_verified']=sequence['state']=='diagnostic'
    finally:
        stop_arm_model_work(root)
        if env:save(root/'model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
    report['state']='diagnostic' if result['state']=='diagnostic' else 'incomplete'
    try:report['gateway_after']=require_live_cost_boundary()
    except RuntimeError as exc:report.update(state='incomplete',spending_stop=str(exc))
    save(root/'status.json',result);save(output/'summary.json',report)
    checked=audit(output);report['audit']=checked
    if checked['state']!='pass':report['state']='incomplete'
    save(output/'native-audit.json',checked);save(output/'summary.json',report)
    return report


def resume_main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--max-messages',type=int,default=4500)
    parser.add_argument('--question-dir',type=Path)
    args=parser.parse_args(argv);report=resume(args.output,max_messages=args.max_messages,question_dir=args.question_dir)
    print(report['state']);return int(report['state']!='diagnostic')


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
