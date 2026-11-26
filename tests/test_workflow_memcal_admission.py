"""Offline admissions only. No transport, credentials, authentic platform export or paid calls."""
import json
from pathlib import Path

import pytest

from pacbench.workflows import capability as cap
from pacbench.providers.memcal import site as workflow_memcal
from pacbench.canary_capability import binding
from pacbench.live_dispatch import fresh_effect, mark_possible, operation_id, process_identity
from pacbench.openrouter_route import with_route
from pacbench.provider_versions import MEMCAL_REVISION, MEMCAL_WEEK_REVISION, MEMCAL_WEEK_CONDITION
from pacbench.providers.memcal.contract import manifest
from pacbench.workflows.progress import WorkflowProgress


def setup_site(tmp_path):
    campaign = tmp_path/'campaign'/'memcal'
    campaign.mkdir(parents=True)
    settings = campaign/'operator.json'
    settings.write_text(json.dumps({'confirmed_by':'fixture', 'confirmed_at':'2026-01-01T00:00:00+00:00',
                                   'auto_recharge':'off'}))
    site = workflow_memcal.MemCalSite('synthetic-account', str(tmp_path/'absent-synthetic-home'),
                                      str(campaign/'memcal-home'), 'bluebubbles',
                                      'mail.example.invalid', 1, 'synthetic@example.invalid', 'https://bb.example.invalid')
    kwargs = dict(canary=True,prepaid={'remaining_usd':'1'},auto_recharge_evidence=binding(settings))
    return campaign, site, kwargs


def test_missing_mac_and_mail_readers_leave_scope_uncreated(tmp_path, monkeypatch):
    campaign, site, kwargs = setup_site(tmp_path)
    monkeypatch.setattr(workflow_memcal, '_pin', lambda: pytest.fail('attempted native access outside declared site'))
    with pytest.raises(workflow_memcal.MemCalSiteError):
        cap.create_memcal_scope(campaign, site, **kwargs)
    assert not (campaign/'workflow-scope.json').exists()


@pytest.fixture
def scope(tmp_path, monkeypatch, request):
    # Explicitly replace the unavailable external source only for transport-negative controls.
    campaign, site, kwargs = setup_site(tmp_path)
    access = dict(state='source-access-verified',memcal_revision=MEMCAL_REVISION,imessage_backend='bluebubbles',
                  imap={'security':'SSL', 'folders':{f:{'mode':'readonly','uidvalidity':'4'} for f in ('INBOX','Sent')}})
    if getattr(request, 'param', MEMCAL_REVISION) == MEMCAL_WEEK_REVISION:
        monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION', MEMCAL_WEEK_CONDITION)
        access.update(manifest())
    else:
        monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION', 'stable-0.8.11')
    monkeypatch.setattr(workflow_memcal, 'inspect_memcal_site', lambda site: access)
    bound = cap.create_memcal_scope(campaign, site, **kwargs)
    monkeypatch.setenv(cap.ENV,json.dumps(bound))
    c = cap.load()
    from pacbench.workflows.source_plan import SourcePlan
    docs, turns, _ = cap.schedule(c['spec_sha256'], c['canary'])
    all_docs, _, _ = cap.schedule(c['spec_sha256'],False)
    with SourcePlan(campaign/'workflow',docs,turns,all_source_documents=all_docs) as plan:
        assert plan.digest == c['plan_sha256']
    return c, bound


def test_scope_rejects_changed_release_and_incomplete_grants(scope, monkeypatch):
    c, bound = scope
    with pytest.raises(ValueError, match='unqualified mixed'):
        cap._access(dict(c['access'],imap={'folders':{'INBOX':{'mode':'readonly'}}}),c['imessage_backend'])
    c['plan_sha256'] = 'other'
    path = Path(bound['path'])
    path.write_text(json.dumps(c))
    monkeypatch.setenv(cap.ENV,json.dumps(binding(path)))
    with pytest.raises(ValueError, match='frozen release'):
        cap.load()


def context(c, bound, progress):
    scope = cap._memcal_schedule(c['spec_sha256'], c['canary'])
    _, _, nights, turns, last = scope
    meta = {k:c[k] for k in ('account','store','spec_sha256','canary','plan_sha256')}
    meta.update(source_basis=c['frozen_inputs'],protocol='memcal-incremental-v1',evidence_kind='native')
    for name, val in [('identity', meta), ('evidence_kind','native'), ('workflow_scope',bound),
                      ('plan_registered',True), ('initial_account_verified',True)]:
        progress.bind(name,val)
    for index, turn, _ in turns.values():
        progress.plan_turn(index,turn)
    store = Path(c['store'])
    store.mkdir(exist_ok=True)
    (store/'memcal.db').touch()
    progress.bind('memcal_activation', dict(scope=bound,source_map='mapping',store=c['store'],store_file=cap._store_file(c),
                                           controller=c['controller'],driver=process_identity()))
    cutoff, rows = next(iter(nights.items()))
    clock = dict(cutoff=cutoff,as_of=cutoff,source_map_sha256='mapping',observation_sha256='1'*64)
    accepted = dict(accepted=True,native_id='1',native_cursors={'imessage':{'rowid':4},'email':{'uid':2}},
                    source_map_sha256='mapping',source_observation_sha256='1'*64,
                    rows={sid:{'source_sha256':s,'archive_sha256':s} for sid,s in rows.items()})
    progress.effect('source-clock',cutoff,[],lambda:clock)
    progress.effect('sync',cutoff,[],lambda:accepted)
    return cutoff, accepted, turns


def row_for(c, bound, slot, role):
    return dict(dispatch_execution='prepared',dispatch_events=[],dispatch_context={'attempt_id':'local-test'},
                dispatch_tracking_condition='direct-process-evidence-v1',dispatch_owner=process_identity(),
                request_sha256='synthetic',workflow_root=c['root'],workflow_capability=bound, workflow_slot=slot,
                project=c['projects']['memcal'][role])


@pytest.mark.parametrize('scope', [MEMCAL_REVISION, MEMCAL_WEEK_REVISION], indirect=True)
def test_live_callback_binds_role_attempt_frozen_release_and_final_dispatch(scope):
    c, bound = scope
    projects = c['projects']['memcal']
    body = with_route({'messages':[{'role':'user','content':'native dream'}]},profile=cap.PROFILE)
    directory = Path(c['root'])/'workflow'
    # Deliberately no JSONL. This path must rely on the SQLite operation and kernel lease.
    with WorkflowProgress(directory,'test-run',create=True) as progress:
        cutoff, accepted, turns = context(c,bound,progress)
        rows = []

        def dream():
            claim = progress.active_effect('dream',cutoff)
            with WorkflowProgress(directory,progress.run_key) as other_handle:
                with pytest.raises(ValueError,match='no live owner'):
                    with cap.memcal_operation(other_handle,'dream',cutoff,accepted):
                        pytest.fail('pending SQLite row alone authorized admission')
            with fresh_effect(progress,'dream',cutoff,claim['attempt_id']):
                with cap.memcal_operation(progress,'dream',cutoff,accepted):
                    permit = cap.admission(projects['memory'],cap.PROFILE,body)
                    slot = permit['slot']
                    assert slot == dict(kind='ingest',batch_id=cutoff,provider='memcal',checkpoint_effect=claim)
                    row = row_for(c,bound,slot,'memory')
                    mark_possible(row,'offline-test')
                    assert row['dispatch_execution'] == 'ambiguous'
                    with pytest.raises(ValueError,match='different role'):
                        cap.admission(projects['driver'],cap.PROFILE,body)
                    with pytest.raises(ValueError,match='no live MemCal judge'):
                        cap.admission(projects['judge'],cap.PROFILE,body)
                    with pytest.raises(ValueError,match='exact provider'):
                        cap.admission(projects['memory'],cap.PROFILE,dict(body,provider={'allow_fallbacks':True}))
                    other = dict(row,workflow_slot={**slot,'checkpoint_effect':{**claim,'attempt_id':'other'}})
                    assert operation_id(row) != operation_id(other)
                    with pytest.raises(ValueError,match='changed before dispatch'):
                        cap.confirm_memcal_sender(other)
                    from pacbench.providers.memcal.capability import observed_dream
                    with observed_dream() as observe:
                        observe('propose_wave', {'kind':'main','entities':['person:fixture'],'requests':1})
                        assert cap.admission(projects['memory'],cap.PROFILE,body)['slot'] == slot
                        with pytest.raises(ValueError,match='predecessors completed'):
                            observe('propose_wave', {'kind':'second-look','entities':['person:fixture'],'requests':1})
                        observe('propose_request', {'index':1})
                        observe('propose_wave', {'kind':'second-look','entities':['person:fixture'],'requests':1})
                        second = cap.admission(projects['memory'],cap.PROFILE,body)['slot']
                        repeated = cap.admission(projects['memory'],cap.PROFILE,body)['slot']
                        assert operation_id(row) != operation_id(row_for(c,bound,second,'memory'))
                        assert second == repeated
                        cap.confirm_memcal_sender(row_for(c,bound,second,'memory'))
                        observe('propose_request', {'index':1})
                        with pytest.raises(ValueError,match='Dream wave changed'):
                            cap.confirm_memcal_sender(row_for(c,bound,second,'memory'))
                    rows.append(row_for(c,bound,slot,'memory'))
                    # A dead callback's saved file cannot be reused even while its controller lives.
                    old = (directory/'active-native-operation.json').read_bytes()
            (directory/'active-native-operation.json').write_bytes(old)
            with pytest.raises((ValueError,FileNotFoundError)):
                mark_possible(rows[0],'offline-test')
            (directory/'active-native-operation.json').unlink()
            return dict(run_id=7,finished_at='2026-01-01',pending_spool=0)

        result = progress.effect('dream',cutoff,accepted,dream,role='memory')
        assert result['run_id'] == 7 and rows[0]['dispatch_execution'] == 'prepared'
        assert not (directory/'workflow.jsonl').exists()

        index, turn, _ = next(iter(turns.values()))
        ready = dict(native_id='1',ready=True,readback=result,new_source_ids=list(accepted['rows']),
                     source_cursor=accepted['native_cursors'])
        progress.record_observation('dream',cutoff,'native-ready',ready)
        progress.advance_cutoff(cutoff)
        inputs = dict(ordinal=index,prompt=turn.prompt,at=turn.at,session=None,cutoff=cutoff)

        def ask():
            claim = progress.active_effect('answer',turn.id)
            with fresh_effect(progress,'answer',turn.id,claim['attempt_id']):
                with cap.memcal_operation(progress,'answer',turn.id,inputs):
                    tools = [{'type':'function','function':{'name':n}} for n in cap.HERMES_TOOLS['memcal']]
                    current = c['access']['memcal_revision'] == MEMCAL_WEEK_REVISION
                    if current:
                        tools = json.loads((Path(__file__).parent/'fixtures/memcal-hermes-0.8.12.json').read_bytes())
                    actual = dict(body,messages=[{'role':'user','content':turn.prompt}],tools=tools)
                    assert cap.admission(projects['driver'],cap.PROFILE,actual)['slot']['question_id'] == turn.id
                    wrapped = turn.prompt + '\n\n<memory-context>\nA saved memory.\n</memory-context>'
                    assert cap.admission(projects['driver'],cap.PROFILE,dict(actual,messages=[
                        {'role':'user','content':wrapped}]))['slot'] == cap.admission(projects['driver'],cap.PROFILE,actual)['slot']
                    all_docs, _, _ = cap.schedule(c['spec_sha256'], False)
                    future = next(b for d in reversed(all_docs) for b in d.bodies if len(b)>40)
                    with pytest.raises(ValueError,match='unavailable source'):
                        cap.admission(projects['driver'],cap.PROFILE,dict(actual,messages=[
                            {'role':'user','content':turn.prompt}, {'role':'tool','content':future}]))
                    with pytest.raises(ValueError,match='question or native tool'):
                        cap.admission(projects['driver'],cap.PROFILE,dict(actual,messages=[
                            {'role':'user','content':turn.prompt+'\n\nextra user instruction'}]))
                    with pytest.raises(ValueError,match='question or native tool|driver schemas'):
                        cap.admission(projects['driver'],cap.PROFILE,dict(actual,tools=[]))
                    if current:
                        changed = {**tools[0], 'function':{**tools[0]['function'], 'description':'changed'}}
                        for drift in (tools[1:], tools+[tools[0]], tools+[{'type':'function','function':{'name':'memcal_open_page'}}],
                                      [changed, *tools[1:]]):
                            with pytest.raises(ValueError, match='driver schemas'):
                                cap.admission(projects['driver'],cap.PROFILE,dict(actual,tools=drift))
                    with pytest.raises(ValueError,match='question or native tool'):
                        cap.admission(projects['driver'],cap.PROFILE,dict(actual,messages=[{'role':'user','content':'other'}]))
            return {'fixture':True}
        progress.effect('answer',turn.id,inputs,ask,role='driver')


def test_native_handoff_requires_dead_owner_kernellock_clean_effects_and_same_store(scope):
    import subprocess
    import sys

    c, bound = scope
    directory = Path(c['root'])/'workflow'
    # Real second process, no provider. Process death and PID+boot identity are OS observations.
    worker = subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.read()'],stdin=subprocess.PIPE)
    try:
        old = process_identity(worker.pid)
        c = dict(c,controller=old)
        with WorkflowProgress(directory,'test',create=True) as progress:
            cutoff, accepted, _ = context(c,bound,progress)
            gateway = process_identity()
            proof = dict(gateway_identity=[gateway['pid'],gateway['start_ticks']],sessions=0,
                         source={'source_map_sha256':'mapping','store_path':str(Path(c['store'])/'memcal.db'),'cutoff':None})
            with pytest.raises((ValueError, FileNotFoundError)):
                cap._transfer(progress,c,0,old,lambda: pytest.fail('no account lock'))
            with progress.account_owner():
                with pytest.raises(ValueError, match='still live'):
                    cap._transfer(progress,c,0,old,lambda: pytest.fail('two live controllers'))
                worker.communicate(timeout=3)
                with pytest.raises(ValueError,match='readback differs'):
                    cap._transfer(progress,c,0,old,lambda: {**proof,'gateway_identity':[17,1]})
                with pytest.raises(ConnectionError):
                    progress.effect('native-write', 'a', {}, lambda: (_ for _ in ()).throw(ConnectionError()))
                with pytest.raises(ValueError,match='unresolved external effect'):
                    cap._transfer(progress,c,0,old,lambda: pytest.fail('took over ambiguous native write'))
                progress.reconcile_effect('native-write','a',{},lambda claim: (
                    {'synthetic':'verified'}, {'operation':claim,'native_evidence':{'offline-witness':'accepted'}}))
                cap._transfer(progress,c,0,old,lambda: proof)
                assert cap._native_owner(progress,c) == (1,process_identity())
                with pytest.raises(ValueError,match='still live'):
                    cap._transfer(progress,c,1,process_identity(),lambda: pytest.fail('stole active stream'))

                def new_dispatch():
                    claim = progress.active_effect('dream',cutoff)
                    with fresh_effect(progress,'dream',cutoff,claim['attempt_id']):
                        with cap.memcal_operation(progress,'dream',cutoff,accepted):
                            assert cap._native_lease(c,c['projects']['memcal']['memory'])['checkpoint_effect'] == claim
                    return {'fixture':True}

                # The scope keeps its original identity, while newly created effects now belong
                # to the requalified process. No previously dispatched operation is sent again.
                from unittest.mock import patch
                with patch.object(cap,'load',return_value=c):
                    progress.effect('dream',cutoff,accepted,new_dispatch,role='memory')
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


def test_foreign_process_cannot_consume_native_sender_lease(scope):
    import subprocess
    import sys

    c, bound = scope
    with WorkflowProgress(Path(c['root'])/'workflow','test',create=True) as p:
        cutoff, accepted, _ = context(c,bound,p)
        def native():
            claim = p.active_effect('dream',cutoff)
            with fresh_effect(p,'dream',cutoff,claim['attempt_id']):
                with cap.memcal_operation(p,'dream',cutoff,accepted):
                    cmd = """
from pacbench.workflows import capability as cap
scope=cap.load()
try:
    cap._native_lease(scope,scope['projects']['memcal']['memory'])
except ValueError as exc:
    assert 'sender' in str(exc)
else:
    raise AssertionError('foreign sender received admission')
"""
                    subprocess.run([sys.executable,'-c',cmd],check=True,timeout=10)
            return {'fixture':True}
        p.effect('dream',cutoff,accepted,native,role='memory')
