"""Run one published Pizza Day question against a fresh native document provider."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .canary_capability import binding, digest, read, sha
from .live_dispatch import CONDITION, process_identity
from .pizza_day_controller import AT, BASIS, DAY, DOCUMENT, QUESTION, QUESTION_AT, SPEC, inputs
from .workflow_tracks import HERMES_TOOLS, PROVIDERS

SCOPE = 'pizza-day-native-document-v1'
ARMS = tuple(name for name in PROVIDERS if name != 'memcal')


def projects(root):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-' + root.name + '-'
    return {root.name:{r:prefix+r for r in ('driver','memory','judge')}}


def validate(scope, bound):
    root = Path(scope['root'])
    if (root != root.resolve() or root.name not in ARMS or scope['provider'] != root.name
            or scope['run_id'] != root.parent.name or scope['frozen_inputs'] != BASIS or scope['spec_sha256'] != SPEC
            or Path(bound['path']) != root/'workflow-scope.json' or scope['projects'] != projects(root)
            or scope['protocol'] != 'pizza-week-conversation-v2'):
        raise ValueError('invalid Day provider binding')


def owned(controller):
    pid = os.getpid()
    while pid > 1:
        if pid == controller['pid']:
            return process_identity(pid) == controller
        pid = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[1])
    return False


def admit(scope, project, body):
    from .workflow_grading import claimed_request
    from .workflow_progress import WorkflowProgress
    from .workflow_capability import ENV, PROFILE, native_user_question
    from .hermes_gateway import _identity
    from .openrouter_route import validate_request
    validate_request(body, profile=PROFILE)
    root = Path(scope['root'])
    name = root.name
    roles = scope['projects'][name]
    with WorkflowProgress(root/'workflow',scope['run_key']) as p:
        if project != roles['judge'] and not owned(scope['controller']):
            daemon = p.binding('independent-memory-process')
            if name != 'hindsight' or project != roles['memory'] or not daemon or not owned(daemon):
                raise ValueError('request outside qualified provider process tree')
        if p.binding('workflow_scope') != json.loads(os.environ[ENV]) or not p.binding('preflight'):
            raise ValueError('native provider preflight binding missing')
        slot = None
        if project == roles['judge']:
            slot = claimed_request(scope,project,body)
        elif p.active_effect('ingest',DAY,DOCUMENT) and project == roles['memory']:
            if sha(root/'sources'/DAY/'messages.txt') != DOCUMENT:
                raise ValueError('wrong daily transcript')
            slot = dict(kind='ingest',batch_id=DAY)
        elif p.last_cutoff() == AT and p.active_effect('answer','pizza-plan',QUESTION):
            if project == roles['driver']:
                users = [r['content'] for r in body['messages'] if r['role']=='user']
                if (len(users)!=1 or not native_user_question(users[0], QUESTION) or
                    sorted(t['function']['name'] for t in body.get('tools',[])) != sorted(HERMES_TOOLS[name])
                    or (name != 'honcho' and process_identity() != p.binding('gateway-process'))):
                    raise ValueError('native Day driver, question or tools differ')
                if name == 'honcho':
                    _identity(root/'hermes-home',os.getpid())
            if project in (roles['driver'],roles['memory']):
                slot = dict(kind='answer',question_id='pizza-plan')
        elif p.active_effect('chat-close','pizza-plan') and project == roles['memory']:
            slot = dict(kind='cleanup',question_id='pizza-plan')
        if not slot:
            raise ValueError('no active Day operation for this role')
        return dict(root=str(root), capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                    slot={**slot,'provider':name}, projects=[project])


def reopen_reme(old, root, source_ids):
    """Recover only a verified source-only prefix, using the stock persisted index."""
    from .gateway_reuse import atomic, clone_observation
    from .native_checkpoint_sources import REME_REOPEN
    from .observational_receipts import RunReceiptJournal
    from .workflow_progress import WorkflowProgress
    from . import harness
    if old.name != 'reme' or root.name != 'reme' or old == root:
        raise ValueError('unsupported recovery')
    scope = json.loads((old/'workflow-scope.json').read_text())
    rows = RunReceiptJournal(old/'receipts.jsonl').snapshot()
    if (scope.get('admission') != SCOPE or scope['frozen_inputs'] != BASIS or
            any(r.get('state') != 'settled' or r['dispatch_context']['phase'] != 'ingest' for r in rows)):
        raise ValueError('original prefix has uncertain or non-import calls')
    with WorkflowProgress(old/'workflow',scope['run_key']) as p:
        with p.account_owner():
            if p.get_effect('answer','pizza-plan') or p.active_effect('answer','pizza-plan') or p.get_verified('pizza-plan'):
                raise ValueError('an answer was already attempted')
            receipt = read(p.get_effect('ingest',DAY))
            native = receipt['result']
            original = old/'reme-workspace'
            pid = json.loads((old/'native-server-setup.json').read_text())['pid']
            log = (old/'reme-native-service.private.log').read_bytes()
            if (Path(f'/proc/{pid}').exists() or b'Application shutdown complete.' not in log
                    or f'Finished server process [{pid}]'.encode() not in log
                    or not receipt['ready'] or native['source_ids'] != list(source_ids)
                    or sha(original/native['resource_path']) != DOCUMENT
                    or sha(original/native['card_path']) != native['card_sha256']):
                raise ValueError('original ReMe closure or native resource/card readback differs')
            clone = clone_observation(original, root/'reme-workspace')
            replica = root/'index-check'
            clone_observation(original,replica)
    expected = root/'expected-card.json'
    atomic(expected,[{k:native[k] for k in ('card_path','resource_path')}])
    process = subprocess.run([str(harness.REPO/'vendor/venv-reme/bin/python'),'-c',REME_REOPEN,
        str(replica),str(expected)],cwd=root,capture_output=True,text=True,timeout=45)
    if process.returncode:
        raise ValueError('stock ReMe index could not reopen: '+process.stderr[-300:])
    reopened=json.loads(process.stdout.strip().splitlines()[-1])
    if not reopened['cards'][0]['keyword_hit_ids'] or reopened['jobs_started']:
        raise ValueError('retained ReMe card is unindexed')
    atomic(root/'reopened.json',dict(original=binding(old/'import.json'),manifest=clone['checkpoint'],native=reopened))
    return receipt


def run(root, preparation, confirmation, *, retained=None):
    from . import workflow_capability as cap, harness
    from .checkpoint_clock import CheckpointClock
    from .context_isolation import validate_recall_environment
    from .document_ingestion import prepare_sources, ingest_day, record_recall_isolation
    from .gateway_client import client_settings
    from .gateway_reuse import PersistentGateway, atomic
    from .model_calls import ModelClient
    from .native_pilot import provision, probe, stop_arm_model_work
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .preparation_audit import audit_prepared
    from .workflow_grading import grade_verified
    from .workflow_receipts import receipt_summary
    from .workflow_memcal_live import run_environment
    from .workflow_metrics import progress_metrics
    from .workflow_native import WarmHermes
    from .workflow_progress import WorkflowProgress
    from .workflow_source_plan import SourcePlan
    from .workflow_stream import event_log
    from .workflow_runner import Condition, captured_turn

    rows, doc, turn = inputs(preparation)
    name = root.name
    if audit_prepared(preparation)['state'] != 'pass' or PROFILE_NAME != cap.PROFILE:
        raise ValueError('published preparation and Luna profile required')
    validate_recall_environment()
    confirm = binding(confirmation)
    cap.confirm_setting(read(confirm))
    root.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    role = projects(root)[name]
    directory = root/'workflow'
    run_key = digest([str(root),BASIS,SPEC])
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
        PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1', PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
        PACBENCH_DISPATCH_TRACKING=CONDITION,PACBENCH_STOP_FILE=str(root/'STOP'),PACBENCH_IMPORT_ROOT=str(root),
        PACBENCH_OBSERVATIONAL_COHORT_STOP=str(cap.campaign_stop(root)),
        PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'))
    with run_environment(settings):
        fetch_catalog(profile=PROFILE_NAME)
        scope=dict(root=str(root),admission=SCOPE,provider=name,run_id=root.parent.name,frozen_inputs=BASIS,
                   protocol='pizza-week-conversation-v2',spec_sha256=SPEC,projects=projects(root),run_key=run_key,
                   controller=process_identity(),prepaid=credit_snapshot(),auto_recharge_evidence=confirm,
                   campaign_stop=str(cap.campaign_stop(root)))
        atomic(root/'workflow-scope.json',scope)
        bound=binding(root/'workflow-scope.json')
        with run_environment({cap.ENV:json.dumps(bound)}), SourcePlan(directory,[doc],[turn]) as plan, WorkflowProgress(directory,run_key,create=True) as p, p.account_owner():
            atomic(directory/'run.json',dict(run_key=run_key,evidence_kind='native',started_unix=time.time(),scope=bound))
            ledger=cap.budget()
            result={'state':'blocked','provider':name,'import_ready':False}
            p.plan_turn(0,turn)
            p.bind('workflow_scope',bound)
            p.bind('evidence_kind','native')
            p.bind('canary',False)
            try:
                batch,=prepare_sources(root,rows,'America/New_York',native_fixtures=False)
                if batch['at']!=AT or sha(root/batch['source_file'])!=DOCUMENT:
                    raise ValueError('wrong published document')
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    recovered=None
                    if retained and name=='reme':
                        recovered=reopen_reme(retained,root,doc.source_ids)
                    elif retained and name!='honcho':
                        raise ValueError('unsupported retained import')
                    options={}
                    if name == 'honcho':
                        from provider_adapters.honcho_enrollment import initial_at
                        options['honcho_simulated_at']=initial_at(rows)
                        if retained:
                            from .pizza_day_honcho import source_snapshot
                            recovered, options=source_snapshot(retained,root,doc)
                    env=provision(root,name,model=MODEL,local_timezone='America/New_York',inference_route='openrouter',**options)
                    capability=probe(root,name,env)
                    atomic(root/'provider-probe.json',capability)
                    if not capability['available']:
                        raise RuntimeError('native provider unavailable: '+capability['reason'])
                    from .tool_policy import policy_record
                    toolnames=HERMES_TOOLS[name]
                    policy=policy_record(name,list(toolnames))
                    env['PACBENCH_SOURCE_ARGUMENT_RULES']=json.dumps(policy['source_argument_rules'])
                    atomic(root/'tool-policy.json',policy)
                    if name == 'hindsight':
                        # The documented local_external launcher daemonizes before ingestion.
                        # Bind the actual listener, its profile HOME and the inherited private run scope.
                        import psutil
                        from urllib.parse import urlsplit
                        config=json.loads((root/'hermes-home/hindsight/config.json').read_text())
                        endpoint=urlsplit(config['api_url'])
                        if endpoint.hostname!='127.0.0.1' or not endpoint.port:
                            raise ValueError('expected a run-owned loopback Hindsight bank')
                        listeners={c.pid for c in psutil.net_connections(kind='tcp') if c.status=='LISTEN' and
                                   c.laddr.port==endpoint.port and c.laddr.ip in ('127.0.0.1','0.0.0.0') and c.pid}
                        pid,=listeners
                        environment=psutil.Process(pid).environ()
                        if (environment.get('HOME')!=str(root/'fallback-home') or environment.get(cap.ENV)!=os.environ[cap.ENV]
                                or environment.get('PACBENCH_RECEIPT_JOURNAL')!=str(root/'receipts.jsonl')):
                            raise ValueError('Hindsight listener belongs to another account')
                        p.bind('independent-memory-process',process_identity(pid))
                    p.bind('preflight',binding(root/'provider-probe.json'))
                    home=root/'hermes-home'
                    record_recall_isolation(root)
                    def ingest():
                        receipt=ingest_day(name,root/'sources'/DAY,root,env,AT,'America/New_York')
                        atomic(root/'import.json',receipt)
                        if not receipt['ready'] or receipt['document_sha256'] != DOCUMENT or receipt['source_ids'] != list(doc.source_ids):
                            raise ValueError('native import did not prove readiness; inspect import.json')
                        return binding(root/'import.json')
                    if recovered is None:
                        p.effect('ingest',DAY,DOCUMENT,ingest,role='memory')
                    else:
                        atomic(root/'import.json',recovered)
                        p.bind('reopened-import',binding(root/'reopened.json'))
                    result['import_ready']=True
                    p.advance_cutoff(AT)
                    record_recall_isolation(root)
                    env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'))
                    env=harness.turn_env(QUESTION_AT if name=='honcho' else AT,home,','.join(toolnames),env)
                    clock=CheckpointClock(home,AT,SPEC)
                    if name in ('mem0','memos'):
                        target=Path(env[{'mem0':'MEM0_DIR','memos':'MEMOS_HOME'}[name]]).resolve()
                        if not target.is_relative_to(root) or not target.is_dir():
                            raise ValueError('native memory store must be run-owned')
                        (root/(name+'-observation')).symlink_to(target,target_is_directory=True)
                    if name == 'honcho':
                        from .pizza_day_honcho import answer_and_verify
                        answer_and_verify(root,p,env,turn,doc,plan,ledger)
                    else:
                        with tempfile.TemporaryDirectory(prefix='pizza-day-chat-') as cwd, PersistentGateway(
                                home,harness.ISOLATED_BIN.parent/'python',env,cwd=Path(cwd),checkpoint=SPEC,
                                observation_clock=clock,provider=name) as gateway:
                            p.bind('gateway-process',process_identity(gateway.witness['pid']))
                            p.bind('native-pins',gateway.pins)
                            def idle():
                                proof=gateway.fence()
                                if proof['sessions'] or any(not r.get('finished') for r in ledger.snapshot()):
                                    raise ValueError('fresh gateway has open sessions or model calls')
                                return dict(gateway_identity=[proof['pid'],proof['start_time']],pending_native_writes=0,
                                            inflight_requests=0,readback=proof,native_errors=[])
                            frontend=WarmHermes(gateway,clock,idle)
                            def answer():
                                return frontend.ask(QUESTION,at=QUESTION_AT,session_id=None,on_return=lambda value:
                                    p.record_observation('answer',turn.id,'cli-return',value))
                            surface=p.effect('answer',turn.id,QUESTION,answer,role='driver')
                            sid=surface['session_id']
                            def close():
                                frontend.end_chat(sid)
                                return gateway.fence(sid)
                            proof=p.effect('chat-close',turn.id,sid,close,role='memory')
                            surface.update(write_barrier=dict(session_id=sid,pending_writes=0,readback=proof),
                                           source_files=[doc.filename],conversation_times=[turn.at],account=str(root))
                            requests={r['request_id'] for r in surface['model_requests']}
                            surface['attempts']=[dict(id=r['id'],parent=r.get('retry_of'),operation=r['dispatch_context']['operation_id'],
                                origin='unknown') for r in ledger.snapshot() if r['id'] in requests]
                            if {r['id'] for r in surface['attempts']}!=requests:
                                raise ValueError('missing driver receipt')
                            plan.verify_no_leak(surface,turn.at,turn.prompt)
                            condition=Condition(name,'native-document','continuing-account','Hermes '+gateway.pins['hermes'],MODEL,
                                'amazon-bedrock/global',name,PROVIDERS[name].reviewed_version,gateway.pins['hermes_isolation_sha256'],
                                'source timestamps and simulated question clock',toolnames,('imessage',))
                            activity=captured_turn(surface,turn,None,condition,[doc.filename])
                            activity['driver_receipts']=dict(coverage='complete',observed_requests=len(requests),missing=[],reused=[])
                            p.verified_turn(turn,surface,activity)
                            p.mark_turn_done(turn.id)
                            p.seal_execution(1,5)
                    judge=ModelClient(key=client_settings(role['judge'])[1],model=MODEL,record_dir=directory/'model-calls',
                                      url=CHAT_COMPLETIONS_URL,json_mode=True,max_attempts=1)
                    with event_log(directory,time.time(),lane='judge',resume=False) as journal:
                        grade_verified(p,judge,directory,journal,limit=1)
                    result['state']='complete' if p.complete else 'ungraded'
            except Exception as exc:
                result['error']=f'{type(exc).__name__}: {exc}'
                raise
            finally:
                stop_arm_model_work(root)
                result.update(metrics=progress_metrics(p),billing=receipt_summary(root,ledger.snapshot(),root.parent.name),
                              elapsed_s=time.monotonic()-started)
                atomic(root/'result.json',result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider',choices=ARMS)
    for arg in ('campaign','preparation','confirmation'):
        parser.add_argument(arg,type=Path)
    parser.add_argument('--resume-import','--resume-reme-import',dest='resume_import',type=Path)
    args=parser.parse_args()
    run(args.campaign.resolve()/args.provider,args.preparation.resolve(),args.confirmation.resolve(),retained=args.resume_import)


if __name__=='__main__':
    main()
