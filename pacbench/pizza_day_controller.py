"""Replay the published one-question MemCal Pizza Day with the incremental controller."""
import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .canary_capability import binding, read, sha
from .frozen import load_bundle, messages_for
from .semantic_contract import load_contract
from .semantic_grade import Claim
from .workflow_spec import Document, Turn, instant
from .workflow_memcal_live import run_environment

BUNDLE = '176a53acda1d8502886e6b7360d0fdee943e13d2c94f5d04e7b7028cefab4717'
CONTRACT = '30241cab0444520231e6151907f9796140b10b46320614c31f45745b6cf35dfc'
SPEC = '5160c6fbb73e0d12d6a1cec84c2ac54bc93f5ecaa6c2a1cd0a5cfce10ee85529'
DOCUMENT = '56d09bcb7c7ae2d39f9e2b0e55a0768c78745feddcfc02e34097a7ed879d9f98'
SCOPE = 'pizza-day-synthetic-memcal-v1'
DAY = '2026-09-10'
AT = DAY + 'T18:50:00-04:00'
QUESTION_AT = '2026-09-11T09:00:00-04:00'
QUESTION = 'Hey, can you give me a quick recap on our pizza plan?'
BASIS = {'bundle_sha256': BUNDLE, 'contract_sha256': CONTRACT}


def inputs(preparation):
    from .document_ingestion import document_text
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
    from .document_ingestion import prepare_sources
    from .preparation_audit import audit_prepared
    from .provider_versions import verify_memcal_source
    from . import harness
    from .workflow_progress import WorkflowProgress
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
    from .workflow_source_plan import SourcePlan
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


def validate_scope(c, bound):
    from .workflow_capability import _projects
    root=Path(c['root'])
    if (root != root.resolve() or root.name != 'memcal' or Path(bound['path']) != root/'workflow-scope.json'
            or c['provider'] != 'memcal' or c['projects'] != _projects(root) or c['spec_sha256'] != SPEC
            or c['frozen_inputs'] != BASIS or c['run_id'] != root.parent.name or c['account'] != str(root)
            or c['store'] != str(root/'memcal-home') or c['canary'] is not False
            or c['protocol'] != 'pizza-week-conversation-v2' or c['admission'] != SCOPE):
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
    from . import harness, workflow_capability as cap
    from .checkpoint_clock import CheckpointClock
    from .context_isolation import validate_recall_environment
    from .gateway_client import client_settings
    from .gateway_reuse import PersistentGateway, atomic
    from .live_dispatch import CONDITION,process_identity
    from .model_calls import ModelClient
    from .native_pilot import memcal_model_settings
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL,MODEL,PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .provider_versions import verify_memcal_source
    from .workflow_delivery import MemCalReaders
    from .workflow_grading import grade_verified
    from .workflow_receipts import receipt_summary
    from .workflow_memcal_chat import MemCalChatWitness
    from .workflow_memcal_account import _model_config
    from .workflow_metrics import progress_metrics
    from .workflow_native import NativeAccount,WarmHermes
    from .workflow_progress import WorkflowProgress
    from .workflow_runner import Condition
    from .workflow_source_plan import SourcePlan
    from .workflow_stream import process_turn,event_log
    from .workflow_tracks import HERMES_TOOLS
    from memcal import db

    rows, doc, turn = inputs(preparation)
    check = json.loads((root/'preflight.json').read_text())
    if check['state'] != 'preflight-pass' or PROFILE_NAME != cap.PROFILE:
        raise ValueError('preflight and selected prepaid Luna route required')
    validate_recall_environment()
    cap.confirm_setting(read(binding(confirmation)))
    fetch_catalog(profile=cap.PROFILE)
    release = verify_memcal_source(harness.REPO)
    roles = cap._projects(root)
    tools = HERMES_TOOLS['memcal']
    home, store = root/'hermes-home',root/'memcal-home'
    started = time.monotonic()
    with SourcePlan(root/'workflow',[doc],[turn]) as plan, WorkflowProgress(root/'workflow',check['run_key']) as p, p.account_owner():
        accepted = p.get_effect('sync',AT)
        inventory = DayInventory(root,rows)
        inventory.source_inventory()
        scope = dict(provider='memcal',admission=SCOPE,root=str(root), canary=False, account=str(root),store=str(store),
                     controller=process_identity(),protocol='pizza-week-conversation-v2',run_id=root.parent.name,
                     spec_sha256=SPEC,projects=roles,frozen_inputs=BASIS,plan_sha256=plan.digest,
                     auto_recharge_evidence=binding(confirmation),prepaid=credit_snapshot())
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','run'))
    parser.add_argument('campaign',type=Path)
    parser.add_argument('preparation',type=Path)
    parser.add_argument('--billing-confirmation',type=Path)
    args=parser.parse_args()
    root=args.campaign.resolve()/'memcal'
    settings=dict(HOME=str(root/'source-home'),MEMCAL_HOME=str(root/'memcal-home'),MEMCAL_TODAY=AT,TZ='America/New_York',
                  MEMCAL_IMESSAGE_BACKEND='chatdb',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',
                  PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
                  PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
                  PACBENCH_STOP_FILE=str(root/'STOP'),PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent/'ACCOUNT_STOP'),
                  PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'),PACBENCH_IMPORT_ROOT=str(root))
    from .live_dispatch import CONDITION
    settings['PACBENCH_DISPATCH_TRACKING']=CONDITION
    with run_environment(settings):
        if args.action=='preflight':
            result=preflight(root,args.preparation)
        else:
            if not args.billing_confirmation:
                parser.error('run requires --billing-confirmation')
            result=execute(root,args.preparation,args.billing_confirmation)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
