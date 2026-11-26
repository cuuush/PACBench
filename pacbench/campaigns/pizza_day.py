"""Launch and authorize the pinned one-question Pizza Day trials."""
import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from ..canary_capability import binding, digest, read, sha
from pacbench.corpus.sources import load_bundle, messages_for
from pacbench.corpus.contract import load_contract
from ..semantic_grade import Claim
from ..live_dispatch import CONDITION, process_identity
from ..workflows.recording import progress_metrics, receipt_summary
from ..workflows.spec import Document, Turn, instant
from ..workflows.tracks import HERMES_TOOLS, PROVIDERS
from pacbench.providers import run_environment

BUNDLE = '176a53acda1d8502886e6b7360d0fdee943e13d2c94f5d04e7b7028cefab4717'
CONTRACT = '30241cab0444520231e6151907f9796140b10b46320614c31f45745b6cf35dfc'
SPEC = '5160c6fbb73e0d12d6a1cec84c2ac54bc93f5ecaa6c2a1cd0a5cfce10ee85529'
DOCUMENT = '56d09bcb7c7ae2d39f9e2b0e55a0768c78745feddcfc02e34097a7ed879d9f98'
MEMCAL_ADMISSION = 'pizza-day-synthetic-memcal-v1'
DAY = '2026-09-10'
AT = DAY + 'T18:50:00-04:00'
QUESTION_AT = '2026-09-11T09:00:00-04:00'
QUESTION = 'Hey, can you give me a quick recap on our pizza plan?'
BASIS = {'bundle_sha256': BUNDLE, 'contract_sha256': CONTRACT}


def inputs(preparation):
    from ..document_ingestion import document_text
    if sha(preparation/'spec.json') != SPEC:
        raise ValueError('expected the published Pizza Day specification')
    messages = messages_for(load_bundle(preparation/'bundle.json', BUNDLE), 'pizza-day')
    contract = load_contract(preparation/'contract.json')
    check, = json.loads((preparation/'resolved-spec.json').read_text())['checks']
    source_ids = tuple(m['source_id'] for m in messages)
    if (contract.contract_hash != CONTRACT or contract.question != QUESTION or check['prompt'] != QUESTION
            or check['at'] != QUESTION_AT or check['id'] != 'pizza-plan' or len(messages) != len(set(source_ids))
            or len(messages) != 25 or {m['channel'] for m in messages} != {'imessage'}
            or [c.statement for c in contract.claims] != check['facts'] or len(contract.claims) != 5
            or any(not c.evidence or set(c.evidence)-set(source_ids) for c in contract.claims)):
        raise ValueError('frozen Pizza Day sources or criteria differ')
    doc = Document('messages.txt', AT, 'imessage', document_text(messages, AT), source_ids,
                   tuple(m['text'] for m in messages))
    if doc.sha256 != DOCUMENT:
        raise ValueError('published export differs')
    turn = Turn(check['id'], QUESTION_AT, QUESTION, 'pizza-recap', 'initial',
                tuple(Claim(c.id, c.statement, tuple(c.evidence)) for c in contract.claims))
    return messages, doc, turn


class DayInventory:
    def __init__(self, root, rows):
        self.root = root
        self.rows = rows

    def source_inventory(self):
        source = self.root/'sources'/DAY
        mapping = json.loads((source/'chat.fixture.json').read_text())['messages']
        path = self.root/'memcal-home/memcal.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            records = dict((guid,(text,instant(ts),owner)) for guid,text,ts,owner in db.execute(
                "SELECT external_id,text,ts,from_me FROM archive WHERE channel='imessage'"))
            expected = {mapping[r['source_id']]['guid']:(r['text'],instant(r['written_at']),r['from_owner']) for r in self.rows}
            if records != expected or db.execute("SELECT count(*) FROM archive WHERE channel NOT IN ('imessage','agent')").fetchone()[0]:
                raise ValueError('stock MemCal archive differs from the frozen Messages export')
        return ['messages.txt']


def preflight(root, preparation):
    from ..document_ingestion import prepare_sources
    from pacbench.corpus.audit import audit_prepared
    from ..provider_versions import verify_memcal_source
    from .. import harness
    from ..workflows.progress import WorkflowProgress
    from provider_adapters.document_connector import memcal_source_mailbox, collect_memcal_sources
    from memcal import config, db
    from memcal.sources import imessage
    rows, doc, turn = inputs(preparation)
    audit = audit_prepared(preparation)
    if audit['state'] != 'pass':
        raise ValueError(audit['errors'])
    pin = verify_memcal_source(harness.REPO)
    source_home = root/'source-home'
    if imessage.CHAT_DB != source_home/'Library/Messages/chat.db' or Path.home() != source_home:
        raise ValueError('stock reader must resolve only the fresh run-owned synthetic OS home')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    source_home.mkdir(mode=0o700)
    cfg = config.load(home=root/'memcal-home')
    cfg.ensure_dirs()
    plan, = prepare_sources(root, rows, 'America/New_York')
    source = root/'sources'/DAY
    if plan['at'] != AT or (source/'messages.txt').read_text() != doc.text:
        raise ValueError('native fixture chronology differs')
    def collect():
        with memcal_source_mailbox(source, root, cfg) as (staged, mailbox), closing(db.open_db(cfg.db_path)) as conn:
            if mailbox is not None or staged != rows or conn.execute('SELECT count(*) FROM archive').fetchone()[0]:
                raise ValueError('fresh native iMessage-only store required')
            receipt = collect_memcal_sources(source,root,cfg,conn,staged,mailbox)
        inventory = DayInventory(root,rows).source_inventory()
        return dict(read=receipt['ingest']['read'], archived=receipt['ingest']['archived'],
                    collection=receipt['collection'], inventory=inventory, document_sha256=doc.sha256)
    key = hashlib.sha256((str(root)+SPEC+CONTRACT+BUNDLE).encode()).hexdigest()
    from ..workflows.source_plan import SourcePlan
    with SourcePlan(root/'workflow',[doc],[turn]), WorkflowProgress(root/'workflow',key,create=True) as progress, progress.account_owner():
        progress.plan_turn(0,turn)
        accepted = progress.effect('sync',AT,BASIS,collect)
        if (accepted['read'],accepted['archived']) != (25,25):
            raise ValueError('incomplete native source collection')
    report = dict(state='preflight-pass', corpus=BASIS, spec_sha256=SPEC, messages=len(rows), question=turn.prompt,
                  turn_id=turn.id, criteria=[c.id for c in turn.claims], at=turn.at, run_key=key,
                  native=accepted, memcal=pin['revision'], model_calls=0, grading_input_audit=audit)
    (root/'preflight.json').write_text(json.dumps(report,indent=2))
    return report


def validate_memcal(c, bound):
    from ..providers.memcal.capability import projects as memcal_projects
    root=Path(c['root'])
    if (root != root.resolve() or root.name != 'memcal' or Path(bound['path']) != root/'workflow-scope.json'
            or c['provider'] != 'memcal' or c['projects'] != memcal_projects(root) or c['spec_sha256'] != SPEC
            or c['frozen_inputs'] != BASIS or c['run_id'] != root.parent.name or c['account'] != str(root)
            or c['store'] != str(root/'memcal-home') or c['canary'] is not False
            or c['protocol'] != 'pizza-week-conversation-v2' or c['admission'] != MEMCAL_ADMISSION):
        raise ValueError('invalid Pizza Day capability')


def operation_slot(progress, phase, key, inputs, claim, owner, driver):
    accepted = progress.get_effect('sync',AT)
    if not accepted or (accepted['read'],accepted['archived'],accepted['document_sha256']) != (25,25,DOCUMENT):
        raise ValueError('Pizza Day Messages collection must be verified')
    if phase == 'dream' and key == AT and inputs == accepted and claim['role'] == 'memory':
        return {'kind':'ingest','batch_id':key}, claim, owner
    ready = progress.get_effect('dream',AT)
    if (key != 'pizza-plan' or not ready or not ready['finished_at'] or ready['pending_spool'] != 0
            or progress.last_cutoff() != AT or progress.next_ordinal() != 0):
        raise ValueError('Pizza Day native processing or schedule differs')
    if phase == 'answer' and claim['role'] == 'driver' and inputs == dict(ordinal=0,prompt=QUESTION,at=QUESTION_AT,session=None,cutoff=AT):
        return {'kind':'answer','question_id':key}, claim, driver
    saved = progress.get_verified(key)
    if phase == 'chat-close' and saved and claim['role']=='memory' and inputs == {'session_id':saved[1]['session_id']}:
        return {'kind':'cleanup','question_id':key}, claim, driver
    raise ValueError('unexpected Pizza Day operation')


def execute(root, preparation, confirmation):
    from pacbench import harness
    from pacbench.workflows import capability as cap
    from ..checkpoint_clock import CheckpointClock
    from ..context_isolation import validate_recall_environment
    from ..gateway_client import client_settings
    from ..gateway_reuse import PersistentGateway, atomic
    from ..model_calls import ModelClient
    from ..native_pilot import memcal_model_settings
    from ..observational_receipts import credit_snapshot
    from ..openrouter_direct import direct_environment
    from ..openrouter_route import CHAT_COMPLETIONS_URL,MODEL,PROFILE_NAME
    from ..openrouter_run import fetch_catalog
    from ..provider_versions import verify_memcal_source
    from ..workflows.delivery import MemCalReaders
    from ..workflows.grading import grade_verified
    from pacbench.providers.memcal.chat import MemCalChatWitness
    from pacbench.providers.memcal.capability import projects as memcal_projects
    from pacbench.providers.memcal.workflow import _model_config
    from ..workflows.native import NativeAccount,WarmHermes
    from ..workflows.progress import WorkflowProgress
    from ..workflows.runner import Condition
    from ..workflows.source_plan import SourcePlan
    from ..workflows.stream import process_turn,event_log
    from ..workflows.tracks import HERMES_TOOLS
    from memcal import db

    rows, doc, turn = inputs(preparation)
    check = json.loads((root/'preflight.json').read_text())
    if check['state'] != 'preflight-pass' or PROFILE_NAME != cap.PROFILE:
        raise ValueError('preflight and selected prepaid Luna route required')
    validate_recall_environment()
    cap.confirm_setting(read(binding(confirmation)))
    fetch_catalog(profile=cap.PROFILE)
    release = verify_memcal_source(harness.REPO)
    roles = memcal_projects(root)
    tools = HERMES_TOOLS['memcal']
    home, store = root/'hermes-home',root/'memcal-home'
    started = time.monotonic()
    with SourcePlan(root/'workflow',[doc],[turn]) as plan, WorkflowProgress(root/'workflow',check['run_key']) as p, p.account_owner():
        accepted = p.get_effect('sync',AT)
        inventory = DayInventory(root,rows)
        inventory.source_inventory()
        scope = dict(provider='memcal',admission=MEMCAL_ADMISSION,root=str(root), canary=False, account=str(root),store=str(store),
                     controller=process_identity(),protocol='pizza-week-conversation-v2',run_id=root.parent.name,
                     spec_sha256=SPEC,projects=roles,frozen_inputs=BASIS,plan_sha256=plan.digest,
                     auto_recharge_evidence=binding(confirmation),prepaid=credit_snapshot(),
                     campaign_stop=str(cap.campaign_stop(root)))
        with (root/'workflow-scope.json').open('x') as out:
            json.dump(scope,out)
        bound=binding(root/'workflow-scope.json')
        with run_environment({cap.ENV:json.dumps(bound)}):
            ledger = cap.budget()
            result={'state':'incomplete'}
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    driver_url,driver_key=client_settings(roles['memcal']['driver'])
                    memory_url,memory_key=client_settings(roles['memcal']['memory'])
                    _,judge_key=client_settings(roles['memcal']['judge'])
                    env_settings=memcal_model_settings(MODEL,memory_url,memory_key)
                    with run_environment(env_settings):
                        cfg=_model_config(store,MODEL,memory_url,memory_key)
                        harness.provision_home(home,provider_plugin_src=Path(release['source'])/'integrations/hermes/memcal',
                                               provider_name='memcal',model=MODEL,model_provider='openrouter',model_base_url=driver_url)
                        env=harness.turn_env(AT,home,','.join(tools),dict(MEMCAL_HOME=str(store),MEMCAL_SRC=release['source'],
                            PACBENCH_MEMCAL_CONDITION='stable-0.8.11',PACBENCH_PROVIDER_ONLY='1',HERMES_ENABLE_PROJECT_PLUGINS='0',
                            PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'),
                            OPENROUTER_API_KEY=driver_key,OPENROUTER_BASE_URL=driver_url,OPENROUTER_API_BASE=driver_url))
                        clock=CheckpointClock(home,AT,SPEC)
                        with tempfile.TemporaryDirectory(prefix='pizza-day-chat-') as cwd, closing(db.connect(cfg.db_path)) as conn, PersistentGateway(
                                home,harness.ISOLATED_BIN.parent/'python',env,cwd=Path(cwd),checkpoint=SPEC,observation_clock=clock,provider='memcal') as gateway:
                            witness=MemCalChatWitness(gateway,ledger)
                            account=NativeAccount(str(root),'memcal',store,MemCalReaders(conn,cfg),
                                                  WarmHermes(gateway,clock,witness.idle),witness,source_witness=inventory)
                            condition=Condition('memcal','synthetic-messages','continuing-account','Hermes '+gateway.pins['hermes'],
                                MODEL,'amazon-bedrock/global','stock Messages reader on synthetic DB',release['version'],
                                gateway.pins['hermes_isolation_sha256'],'scheduled native question',tools,('imessage',))
                            identity=dict(protocol='memcal-incremental-v1',evidence_kind='native',spec_sha256=SPEC,source_basis=BASIS,
                                          account=str(root),store=str(store),condition=asdict(condition),canary=False,plan_sha256=plan.digest)
                            atomic(root/'workflow/run.json',dict(run_key=p.run_key,identity=identity,evidence_kind='native',started_unix=time.time()))
                            for name,val in dict(workflow_scope=bound,identity=identity,evidence_kind='native',canary=False,
                                initial_account_verified=True,plan_registered=True,gateway=account.frontend.gateway_identity(),
                                memcal_activation=dict(scope=bound,store=str(store),store_file=cap._store_file(scope),
                                                       driver=process_identity(gateway.witness['pid']))).items():
                                p.bind(name,val)
                            def dream():
                                with cap.memcal_operation(p,'dream',AT,accepted):
                                    return account.process_sync()
                            ready=p.effect('dream',AT,accepted,dream,role='memory')
                            p.advance_cutoff(AT,native_ready=ready)
                            with event_log(root/'workflow',time.time(),lane='account',resume=False) as journal:
                                process_turn(0,turn,True,account,condition,plan,p,journal)
                                p.seal_execution(1,5)
                                judge=ModelClient(key=judge_key,model=MODEL,record_dir=root/'workflow/model-calls',
                                                  url=CHAT_COMPLETIONS_URL,json_mode=True,max_attempts=1)
                                grade_verified(p,judge,root/'workflow',journal,limit=1)
                result['state']='complete' if p.complete else 'ungraded'
            finally:
                result.update(metrics=progress_metrics(p),billing=receipt_summary(root,ledger.snapshot(),root.parent.name),
                              elapsed_s=time.monotonic()-started)
                atomic(root/'result.json',result)
    return result


def memcal_main(argv=None):
    from pacbench.workflows import capability as cap

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','run'))
    parser.add_argument('campaign',type=Path)
    parser.add_argument('preparation',type=Path)
    parser.add_argument('--billing-confirmation',type=Path)
    args=parser.parse_args(argv)
    root=args.campaign.resolve()/'memcal'
    settings=dict(HOME=str(root/'source-home'),MEMCAL_HOME=str(root/'memcal-home'),MEMCAL_TODAY=AT,TZ='America/New_York',
                  MEMCAL_IMESSAGE_BACKEND='chatdb',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',
                  PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
                  PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
                  PACBENCH_STOP_FILE=str(root/'STOP'),PACBENCH_OBSERVATIONAL_COHORT_STOP=str(cap.campaign_stop(root)),
                  PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'),PACBENCH_IMPORT_ROOT=str(root))
    settings['PACBENCH_DISPATCH_TRACKING']=CONDITION
    with run_environment(settings):
        if args.action=='preflight':
            result=preflight(root,args.preparation)
        else:
            if not args.billing_confirmation:
                parser.error('run requires --billing-confirmation')
            result=execute(root,args.preparation,args.billing_confirmation)
    print(json.dumps(result,indent=2))


LINEUP_ADMISSION = 'pizza-day-native-document-v1'
ARMS = tuple(name for name in PROVIDERS if name != 'memcal')


def projects(root):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-' + root.name + '-'
    return {root.name:{r:prefix+r for r in ('driver','memory','judge')}}


def validate_lineup(scope, bound):
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


def admit_lineup(scope, project, body):
    from ..workflows.grading import claimed_request
    from ..workflows.progress import WorkflowProgress
    from ..workflows.capability import ENV, PROFILE, native_user_question
    from ..hermes_gateway import _identity
    from ..openrouter_route import validate_request
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
    from ..gateway_reuse import atomic, clone_observation
    from ..native_checkpoint_sources import REME_REOPEN
    from ..observational_receipts import RunReceiptJournal
    from ..workflows.progress import WorkflowProgress
    from .. import harness
    if old.name != 'reme' or root.name != 'reme' or old == root:
        raise ValueError('unsupported recovery')
    scope = json.loads((old/'workflow-scope.json').read_text())
    rows = RunReceiptJournal(old/'receipts.jsonl').snapshot()
    if (scope.get('admission') != LINEUP_ADMISSION or scope['frozen_inputs'] != BASIS or
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


def run_lineup(root, preparation, confirmation, *, retained=None):
    from pacbench.workflows import capability as cap
    from pacbench import harness
    from ..checkpoint_clock import CheckpointClock
    from ..context_isolation import validate_recall_environment
    from ..document_ingestion import prepare_sources, ingest_day, record_recall_isolation
    from ..gateway_client import client_settings
    from ..gateway_reuse import PersistentGateway, atomic
    from ..model_calls import ModelClient
    from ..native_pilot import provision, probe, stop_arm_model_work
    from ..observational_receipts import credit_snapshot
    from ..openrouter_direct import direct_environment
    from ..openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from ..openrouter_run import fetch_catalog
    from pacbench.corpus.audit import audit_prepared
    from ..workflows.grading import grade_verified
    from ..workflows.native import WarmHermes
    from ..workflows.progress import WorkflowProgress
    from ..workflows.source_plan import SourcePlan
    from ..workflows.stream import event_log
    from ..workflows.runner import Condition, captured_turn

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
        scope=dict(root=str(root),admission=LINEUP_ADMISSION,provider=name,run_id=root.parent.name,frozen_inputs=BASIS,
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
                            from .pizza_day_retained import source_snapshot
                            recovered, options=source_snapshot(retained,root,doc)
                    env=provision(root,name,model=MODEL,local_timezone='America/New_York',inference_route='openrouter',**options)
                    capability=probe(root,name,env)
                    atomic(root/'provider-probe.json',capability)
                    if not capability['available']:
                        raise RuntimeError('native provider unavailable: '+capability['reason'])
                    from ..source_policy import policy_record
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
                        from .pizza_day_retained import answer_and_verify
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


def lineup_main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider',choices=ARMS)
    for arg in ('campaign','preparation','confirmation'):
        parser.add_argument(arg,type=Path)
    parser.add_argument('--resume-import','--resume-reme-import',dest='resume_import',type=Path)
    args=parser.parse_args(argv)
    run_lineup(args.campaign.resolve()/args.provider,args.preparation.resolve(),args.confirmation.resolve(),retained=args.resume_import)


def main(argv=None):
    from .pizza_day_retained import honcho_judge_main, recover_main, result_main
    from .pizza_day_review import control_main, review_main

    commands = {
        'memcal': memcal_main,
        'lineup': lineup_main,
        'recover': recover_main,
        'result': result_main,
        'honcho-judge': honcho_judge_main,
        'judge-control': control_main,
        'review': review_main,
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=commands)
    parser.add_argument('arguments', nargs=argparse.REMAINDER, metavar='...')
    args = parser.parse_args(argv)
    return commands[args.command](args.arguments)


if __name__ == '__main__':
    raise SystemExit(main())
