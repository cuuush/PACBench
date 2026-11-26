"""Checkpoint-scoped transport for the real scored evaluator.

Runtime factories own native ingestion, closure/reopen and snapshot witnesses.
This interface cannot qualify a runtime by asserting ready=True. The funded
transport stays disabled until the native production runtime is qualified.
"""
from pathlib import Path
import hashlib
import json,os
from .gateway_reuse import tree_manifest
from .hermes_gateway import GatewayLifecycleError


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_transport(turn):
    """Re-read immutable native receipts, not a caller-supplied success flag."""
    g=turn.hermes_gateway or {}
    if g.get('version')!='checkpoint-session-v1':raise GatewayLifecycleError('Missing checkpoint transport receipt')
    folder=Path(g['receipt']);ready=folder/'answer-ready.json';cleanup=folder/'cleanup.json'
    if sha(ready)!=g['answer_ready_sha256'] or sha(cleanup)!=g['cleanup_sha256']:
        raise GatewayLifecycleError('Checkpoint transport receipt changed')
    r=json.loads(ready.read_text());c=json.loads(cleanup.read_text());native=r['native_turn']
    if any(native.get(k)!=getattr(turn,k) for k in ['prompt','at','answer','session_id','tool_calls','tool_results','prefetch_context','context_isolation']):
        raise GatewayLifecycleError('Returned answer differs from atomic native capture')
    if c.get('state')!='drained' or g.get('native_drain')!='complete':raise GatewayLifecycleError('Native cleanup unverified')
    if r['context_isolation'].get('state')!='pass' or g.get('new_user_count')!=1:
        raise GatewayLifecycleError('Native isolation or sender count unverified')
    e=r.get('observation_clock') or {};d=e.get('descriptor') or {}
    from .checkpoint_clock import audit_question
    witness={'pid':g['pid'],'start_time':g['start_time'],'process_adoption':e.get('gateway_process_adoption')}
    observed=audit_question(Path(d['home']).parent/'reuse-clock',d,turn,witness)
    if any(e.get(k)!=v for k,v in observed.items()):raise GatewayLifecycleError('Native clock evidence changed')
    if g.get('close_condition'):
        from .gateway.preclose import CONDITION,TIMEOUT,qualified
        b=g.get('preclose')
        if g['close_condition']!=CONDITION or b!=c.get('preclose') or not b or sha(b['path'])!=b['sha256']:
            raise GatewayLifecycleError('Native pre-close receipt missing or changed')
        p=json.loads(Path(b['path']).read_text());sample=p.get('observation') or {}
        if (p.get('state')!='quiescent' or p.get('condition')!=CONDITION or p.get('session_id')!=turn.session_id or
            p.get('nonce')!=d['nonce'] or p.get('wait_bound_s')!=TIMEOUT or p.get('native_shutdown_settings_changed') is not False or
            sample.get('pid')!=g['pid'] or sample.get('start_time')!=g['start_time'] or
            sample.get('observer_sha256')!=r['pins']['reuse_observer_sha256'] or
            not qualified(sample,session=turn.session_id,nonce=d['nonce'],after_ns=p['terminal_verified_before_ns'],
                prompt_hash=hashlib.sha256(turn.prompt.encode()).hexdigest())):
            raise GatewayLifecycleError('Native pre-close quiescence proof invalid')
    users=[m['content'] for m in r['answer_visible_records'] if m['role']=='user']
    if len(users)!=g['prior_user_count']+1 or users[-1]!=turn.prompt:
        raise GatewayLifecycleError('Native transcript sender count differs')
    if g['fresh_session'] and len(users)!=1:raise GatewayLifecycleError('Cross-session user history')
    return {'state':'pass','receipt_sha256':sha(ready),'clock_nonce':d['nonce']}


def verify_checkpoint(group,proof,schedule,provider,*,allow_synthetic=False):
    """Bind native receipts and a CLOSED source-only store to this exact prefix."""
    ids=group['ready_batch_ids'];by_id={b['id']:b for b in schedule}
    if proof.get('provider')!=provider or proof.get('ingested_batch_ids')!=ids:
        raise GatewayLifecycleError('Checkpoint includes missing or future sources')
    baseline=Path(proof['baseline']);manifest=tree_manifest(baseline)
    if manifest!=proof.get('manifest'):raise GatewayLifecycleError('Closed checkpoint snapshot changed')
    if set(proof.get('ingestion_receipts',{}))!=set(ids):raise GatewayLifecycleError('Native ingestion receipt coverage missing')
    for identifier in ids:
        binding=proof['ingestion_receipts'][identifier];path=Path(binding['path'])
        if sha(path)!=binding['sha256']:raise GatewayLifecycleError('Native ingest receipt changed')
        r=json.loads(path.read_text());result=r.get('result',{})
        if (r.get('provider')!=provider or r.get('state')!='ready' or r.get('ready') is not True or
            r.get('returncode')!=0 or result.get('ready') is not True or
            r.get('source_ids')!=by_id[identifier]['source_ids'] or
            not by_id[identifier].get('document_sha256') or r.get('document_sha256')!=by_id[identifier]['document_sha256'] or
            result.get('native_errors') or result.get('model_output_truncated')):
            raise GatewayLifecycleError('Native ingestion incomplete or failed')
    # Provider-specific close/reopen witness must come from the native runtime,
    # not successful construction, queue emptiness, or a copied database alone.
    closure=proof.get('native_closure',{})
    if (closure.get('provider')!=provider or closure.get('checkpoint_sha256')!=manifest['sha256'] or
        closure.get('closed_and_reopened') is not True or closure.get('pending_work')!=[] or
        not closure.get('evidence')):
        raise GatewayLifecycleError('Native closed/reopened checkpoint evidence absent')
    for binding in closure['evidence']:
        if sha(binding['path'])!=binding['sha256']:raise GatewayLifecycleError('Native closure evidence changed')
    from .native_checkpoint_sources import PROTOCOL as SOURCE_PROTOCOL
    if proof.get('protocol') != SOURCE_PROTOCOL:
        if not allow_synthetic or proof.get('condition')!='offline-synthetic-storage-fixture':
            raise GatewayLifecycleError('Native source snapshot qualification absent; synthetic readiness is not production proof')
    else:
        if provider in {'memcal','reme'}:
            from .native_checkpoint_sources import verify_collector_snapshot
            verify_collector_snapshot(provider,proof,schedule,ids,manifest)
            return manifest['sha256']
        if provider!='mem0':raise GatewayLifecycleError('Native source snapshot adapter is not qualified for this provider')
        from .hermes_gateway import _live
        from .observation_adapters import MEM0_PLUGIN_SHA256
        for binding in closure['evidence']:
            native=json.loads(Path(binding['path']).read_text());reopened=native.get('native_reopen',{})
            if (native.get('protocol')!=SOURCE_PROTOCOL or native.get('provider')!=provider or
                native.get('checkpoint_sha256')!=manifest['sha256'] or native.get('ingested_batch_ids')!=ids or
                native.get('source_only') is not True or native.get('pending_work')!=[] or
                native.get('pins',{}).get('mem0_sdk')!='2.2.1' or
                native.get('pins',{}).get('mem0_plugin_sha256')!=MEM0_PLUGIN_SHA256 or
                not native.get('native_ingest_worker_pids') or any(_live(pid) for pid in native['native_ingest_worker_pids']) or
                reopened.get('provider')!='mem0' or reopened.get('sqlite_integrity')!='ok' or
                reopened.get('history_namespace_verified') is not True or reopened.get('qdrant_namespace_verified') is not True or
                reopened.get('memory_mutation_requested') is not False or reopened.get('llm_inference_requested') is not False):
                raise GatewayLifecycleError('Native store reopen/quiescence evidence invalid')
            clocks=native.get('source_and_billing_clock_proofs',[])
            from datetime import datetime
            from .document_ingestion import processing_at
            if [row.get('batch_id') for row in clocks]!=ids:
                raise GatewayLifecycleError('Native source clock coverage absent')
            for row in clocks:
                expected=datetime.fromisoformat(processing_at(by_id[row['batch_id']]['at'],provider,'America/New_York'))
                if (abs(datetime.fromisoformat(row['billing_at']).timestamp()-row['real_epoch'])>5 or
                    datetime.fromisoformat(row['provider_at'])<expected or
                    datetime.fromisoformat(row['provider_at'])>=datetime.fromisoformat(group['questions'][0]['at'])):
                    raise GatewayLifecycleError('Native source visibility or real billing-clock evidence invalid')
        for identifier in ids:
            calls=proof['ingestion_receipts'][identifier].get('native_calls',{});document=proof.get('input_documents',{}).get(identifier,{})
            if sha(calls['path'])!=calls['sha256'] or sha(document['path'])!=by_id[identifier]['document_sha256']:
                raise GatewayLifecycleError('Native submission evidence changed')
            text=Path(document['path']).read_text();records=json.loads(Path(calls['path']).read_text())
            if not any(text in m.get('content','') for c in records for m in c['request'].get('messages',[]) if m.get('role')=='user'):
                raise GatewayLifecycleError('Native input is incomplete or differs from frozen document')
    return manifest['sha256']


class CheckpointEvaluator:
    """Serial, no replay. A failure poisons this evaluation, not historical work."""
    def __init__(self,plan,schedule,runtime_factory,output,*,allow_synthetic=False,reused_turns=None):
        if plan['provider'] not in {'mem0','memcal','reme'}:raise ValueError('Staged provider only')
        self.plan=plan;self.schedule=schedule;self.factory=runtime_factory;self.output=Path(output);self.allow_synthetic=allow_synthetic
        self.reused_turns=reused_turns or {}
        self.current=None;self.group=None;self.question=None;self.failed=False;self.sessions={};self.index=0;self.history_transfer=None
        self.questions=[(g,q) for g in plan['groups'] for q in g['questions']]
        # A shared authored conversation crossing ingestion boundaries requires
        # native session migration, not silent conversion to independent chats.
        self.closed=False

    def before_check(self,row,directory):
        if self.failed or self.closed:raise GatewayLifecycleError('Checkpoint recovery required; no replay')
        if self.index>=len(self.questions):raise GatewayLifecycleError('Unexpected additional question')
        group,q=self.questions[self.index]
        if row['id']!=q['id'] or row['at']!=q['at']:raise GatewayLifecycleError('Frozen question order/clock differs')
        if self.group!=group['id']:
            sealed=None
            if self.current is not None:
                needed={later['logical_session'] for _,later in self.questions[self.index:]}
                retained=[sid for sid,state in self.sessions.items() if state['logical_session'] in needed]
                if retained:
                    from .checkpoint_sessions import seal
                    seal_path=self.output/'session-seals'/f'checkpoint-{self.group}.json';seal_path.parent.mkdir(exist_ok=True)
                    sealed=seal(self.current.owner,retained,seal_path)
            self._close_runtime()
            try:
                runtime=self.factory(group)
                self.current=runtime
                verify_checkpoint(group,runtime.proof,self.schedule,self.plan['provider'],allow_synthetic=self.allow_synthetic)
                if sealed:
                    from .checkpoint_sessions import transfer
                    self.history_transfer=transfer(sealed,runtime.home)
                else:self.history_transfer=None
                runtime.__enter__()
            except BaseException:
                self.failed=True;self._close_runtime();raise
            self.group=group['id']
        self.question=q;self.directory=Path(directory);self.index+=1
        self.directory.mkdir(parents=True,exist_ok=True)
        (self.directory/'source-state.json').write_text(json.dumps({'reference_version':3,'at':q['at'],
            'ingested_batches':group['ready_batch_ids'],'checkpoint_sha256':self.current.proof['manifest']['sha256']})+'\n')

    def __call__(self,prompt,*,at,session_id=None,timeout_s=180,**kwargs):
        if self.failed or not self.current or not self.question:raise GatewayLifecycleError('Explicit checkpoint readiness required before dispatch')
        q=self.question
        try:
            if q['id'] in self.reused_turns:
                from .harness import TurnResult
                turn=TurnResult(**json.loads(Path(self.reused_turns[q['id']]).read_text()))
                if (session_id or prompt!=q['prompt'] or at!=q['at'] or turn.prompt!=prompt or turn.at!=at or
                    (turn.hermes_gateway or {}).get('authored_checkpoint_resume') or
                    any(later['logical_session']==q['logical_session'] for _,later in self.questions[self.index:])):
                    raise GatewayLifecycleError('Captured answer reuse cannot lose required native history')
                audit_transport(turn)
                self.current.reuse_observation(q,turn)
                self.reused_turns.pop(q['id'])
                self.sessions[turn.session_id]={'question_id':q['id'],'observation':json.loads((Path(turn.hermes_gateway['receipt'])/'answer-ready.json').read_text())['observation'],'logical_session':q['logical_session']}
                return turn
            if session_id and hasattr(self.current,'reuse_followup'):
                retained=self.current.reuse_followup(q,prompt,at,session_id)
                if retained is not None:return retained
            authored=False
            if session_id:
                previous=self.sessions.get(session_id)
                if not previous or previous['logical_session']!=q['logical_session']:
                    raise GatewayLifecycleError('Cross-question native session is not designated by the frozen contract')
                authored=previous['question_id']!=q['id']
                if authored:
                    if not self.history_transfer or at!=q['at'] or prompt!=q['prompt'] or q.get('dispatched'):
                        raise GatewayLifecycleError('Authored checkpoint transfer is absent or question differs')
                    observation=self.current.observation(q);q['dispatched']=True
                    self.current.owner.adopt_session(self.history_transfer,session_id,observation,q)
                else:observation=previous['observation']
            else:
                if at!=q['at'] or prompt!=q['prompt']:raise GatewayLifecycleError('Frozen prompt/clock differs')
                if q.get('dispatched'):raise GatewayLifecycleError('No automatic question replay')
                observation=self.current.observation(q)
                q['dispatched']=True
            turn,_=self.current.owner.run(prompt,at=at,observation=observation,horizon=q['horizon'],
                timeout=timeout_s,session_id=session_id,authored_resume=authored)
            audit_transport(turn)
            if not session_id or authored:self.sessions[turn.session_id]={'question_id':q['id'],'observation':observation,'logical_session':q['logical_session']}
            return turn
        except BaseException:self.failed=True;raise

    def _close_runtime(self):
        current=self.current;self.current=None
        if current is not None:current.__exit__(None,None,None)

    def close(self):
        try:self._close_runtime()
        finally:self.closed=True


def launch(*,checks,plan,schedule,runtime_factory,root,judge,env,question_dir,
           budget_snapshot,allowlist='',reused_turns=None,**options):
    """Production evaluator entry point, also used with offline wire fixtures.

    Missing native proof blocks dispatch. Paid transport qualification remains a
    separate hard gate in PersistentGateway, not an environment toggle here.
    """
    from .native_checks import run_checks
    if env.get('PACBENCH_DIRECT',os.environ.get('PACBENCH_DIRECT'))=='1':
        from .full_run_capability import ENV,load
        if not env.get(ENV) or not os.environ.get(ENV):
            raise GatewayLifecycleError('Funded checkpoint transport is not qualified; refuse before native ingestion')
        permission=load()
        from .full_run_capability import frozen
        full_plan=frozen()[3]
        if (plan.get('frozen_inputs')!=full_plan['frozen_inputs'] or plan.get('groups')!=full_plan['groups'] or
            len(checks)!=42 or plan.get('questions')!=42 or plan.get('criteria')!=188):
            raise GatewayLifecycleError('Full run requires the full frozen 42/188 contract')
    count=options.get('resume_prefix',0)
    if count:
        proof=json.loads((Path(root)/'resume-proof.json').read_text())
        from .full_run_capability import read
        read(proof['prior_evaluation'])
        if (proof.get('protocol')!='closed-checkpoint-independent-prefix-v1' or proof['count']!=count or
            not proof.get('pending_sessions_disjoint') or len(proof['retained_turns'])!=count):
            raise GatewayLifecycleError('Verified closed prefix proof absent')
        for binding in proof['retained_turns']:read(binding)
    if not plan.get('frozen_inputs') and env.get('PACBENCH_OFFLINE_CONFORMANCE')!='1':
        raise ValueError('Verified frozen r2 bindings required')
    if len(checks)!=plan['questions'] or sum(len(c.claims) for _,c,_ in checks)!=plan['criteria']:
        raise ValueError('Frozen evaluator coverage differs from checkpoint plan')
    expected=[q for g in plan['groups'] for q in g['questions']]
    if len(expected)!=len(checks):raise ValueError('Frozen checkpoint question coverage differs')
    for (row,contract,_),q in zip(checks,expected):
        if (row['id'],row['at'],contract.question,contract.contract_hash)!=(q['id'],q['at'],q['prompt'],q['contract_hash']):
            raise ValueError('Frozen evaluated contract differs')
    # A declared transport capability is followed by actual cold SQLite and
    # stopped-owner proofs. Never silently discard authored history.
    from .native_checks import session_key
    from .checkpoint_sessions import PROTOCOL as SESSION_PROTOCOL
    prepared=json.loads(json.dumps(plan));seen=set()
    for (row,_,_),q in zip(checks,[q for g in prepared['groups'] for q in g['questions']]):
        key=session_key(row,'isolated');q['logical_session']=key
        if key in seen and getattr(runtime_factory,'native_session_checkpoint',None)!=SESSION_PROTOCOL:
            raise GatewayLifecycleError('Authored shared-session migration requires the qualified cold native transport')
        seen.add(key)
    transport=CheckpointEvaluator(prepared,schedule,runtime_factory,root,allow_synthetic=env.get('PACBENCH_OFFLINE_CONFORMANCE')=='1',reused_turns=reused_turns)
    if count:
        before={q['logical_session'] for _,q in transport.questions[:count]}
        after={q['logical_session'] for _,q in transport.questions[count:]}
        if before & after:raise GatewayLifecycleError('Resume cut crosses required native session history')
        transport.index=count
    def before(row,directory):
        if budget_snapshot is not None:budget_snapshot()
        transport.before_check(row,directory)
    try:
        return run_checks(checks,'isolated',Path(root),judge,allowlist,
            {**env,'PACBENCH_STRICT_MEMORY_ONLY':'1','PACBENCH_PROVIDER_NAME':plan['provider']},
            question_dir=question_dir,run_turn=transport,before_check=before,
            budget_snapshot=budget_snapshot,provider_name=plan['provider'],**options)
    finally:transport.close()


def freeze_inputs(preparation,schedule,provider,expected):
    """Bind existing r2 files; never render, repair, or migrate historical source.

    expected contains independently retained raw file hashes, not a hash made up
    by a runtime after it has already ingested the corpus.
    """
    from pacbench.corpus.contract import load_contract
    from .followups import load_questions
    from .observation_adapters import checkpoint_plan
    root=Path(preparation)
    paths=['bundle.json','resolved-spec.json','evaluation-plan.json']
    resolved=json.loads((root/'resolved-spec.json').read_text())
    for row in resolved['checks']:
        paths.extend(f'evaluations/{row["id"]}/{name}.json' for name in ['contract','followup-plan'])
    if set(expected)!=set(paths) or any(sha(root/p)!=expected[p] for p in paths):
        raise ValueError('Frozen r2 input identity/hash differs')
    bundle=json.loads((root/'bundle.json').read_text())
    messages=[m for life in bundle['lives'].values() for m in life['messages']]
    if len(messages)!=142 or len({m['source_id'] for m in messages})!=142 or len(resolved['checks'])!=42:
        raise ValueError('Frozen r2 source/question denominator differs')
    all_ids=[i for b in schedule for i in b['source_ids']]
    if len(all_ids)!=142 or set(all_ids)!={m['source_id'] for m in messages}:
        raise ValueError('Frozen source schedule missing, duplicated or foreign messages')
    documents={};checks=[]
    for row in resolved['checks']:
        directory=root/'evaluations'/row['id'];documents[row['id']]=json.loads((directory/'contract.json').read_text())
        contract=load_contract(directory/'contract.json');policy=load_questions(directory/'followup-plan.json',contract)
        checks.append((row,contract,policy))
    for batch in schedule:
        source=root/batch['source_file']
        if sha(source)!=batch.get('document_sha256'):
            raise ValueError('Frozen source document content differs')
    plan=checkpoint_plan(resolved,schedule,provider,contracts=documents)
    if plan['criteria']!=188:raise ValueError('Frozen r2 criterion denominator differs')
    plan['frozen_inputs']={'protocol':'r2-input-file-bindings-v1','sha256':hashlib.sha256(
        json.dumps(expected,sort_keys=True).encode()).hexdigest(),'messages':142,'questions':42,'criteria':188}
    return checks,plan
