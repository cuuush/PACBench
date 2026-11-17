"""Chronological enrollment must precede source delivery without editing it."""
from copy import deepcopy
from datetime import datetime
import json
import os
from types import ModuleType, SimpleNamespace
import sys

import pytest

from provider_adapters import honcho_enrollment as enrollment


HOST = {'peerName':'user','aiPeer':'hermes','workspace':'isolated'}
BEFORE = '2026-09-10T21:58:00+00:00'
EVENT = '2026-09-10T22:00:00+00:00'
AFTER = '2026-09-10T22:01:00+00:00'


def message():
    return {'source_id':'one','channel':'imessage','thread':'opaque',
        'sender':'Sam','from_owner':False,'text':'Original body.\n🍕',
        'written_at':EVENT,'structured':{'participants':['Me','Sam']}}


def snapshot(plan, joined=BEFORE):
    return {'db_at':AFTER,'memberships':[{'session_id':plan['session_id'],
        'peer_id':peer,'joined_at':joined,'left_at':None}
        for peer in [*plan['source_peers'],plan['observer']]]}


def test_silent_owner_is_enrolled_from_declared_roster_not_future_authors():
    plan=enrollment.source_plans([message()],HOST)[0]
    assert plan['source_peers']==['Sam','user']
    assert plan['participants']==['Me','Sam']
    assert enrollment.initial_at([message()])=='2026-09-10T21:55:00+00:00'


@pytest.mark.parametrize('change',['absent','missing-owner','duplicate','speaker','transition','collision'])
def test_bad_or_changing_source_membership_fails_before_native_operations(change):
    first=message();second=deepcopy(first);second['source_id']='two'
    if change=='absent':first.pop('structured')
    if change=='missing-owner':first['structured']['participants']=['Sam']
    if change=='duplicate':first['structured']['participants']=['Me','Sam','Sam']
    if change=='speaker':first['sender']='Unknown'
    if change=='transition':second['structured']['participants']=['Me','Sam','Maya']
    if change=='collision':first['structured']['participants']=['Me','Sam','hermes']
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.source_plans([first,second],HOST)


@pytest.mark.parametrize('change',['late','left','missing','duplicate','unexpected','future-native'])
def test_original_event_requires_actual_active_membership_window(change):
    plan=enrollment.source_plans([message()],HOST)[0];state=snapshot(plan)
    if change=='late':state['memberships'][0]['joined_at']=AFTER
    if change=='left':state['memberships'][0]['left_at']=EVENT
    if change=='missing':state['memberships'].pop()
    if change=='duplicate':state['memberships'].append(deepcopy(state['memberships'][0]))
    if change=='unexpected':state['memberships'][0]['peer_id']='outsider'
    if change=='future-native':
        state['db_at']=BEFORE;state['memberships'][0]['joined_at']=EVENT
    with pytest.raises(enrollment.EnrollmentError):enrollment.validate_windows([plan],state)
    enrollment.validate_windows([plan],snapshot(plan))


def native_fixture(tmp_path,monkeypatch,*,now=BEFORE,joined=None):
    source=tmp_path/'day';source.mkdir();root=tmp_path/'arm';(root/'hermes-home').mkdir(parents=True)
    (source/'messages.json').write_text(json.dumps([message()],ensure_ascii=False))
    (root/'hermes-home/honcho.json').write_text(json.dumps({
        'baseUrl':'http://127.0.0.1:1','hosts':{'hermes':HOST}}))
    state={'db_at':now,'memberships':[]};calls=[]
    class Peer:
        def __init__(self,name):self.name=name
    class Session:
        def __init__(self,name):self.id=name
        def add_peers(self,peers):
            calls.append([p[0].name if isinstance(p,tuple) else p.name for p in peers])
            state['memberships']=[r for r in state['memberships'] if r['session_id']!=self.id]+[
                {'session_id':self.id,'peer_id':name,'joined_at':joined or state['db_at'],'left_at':None}
                for name in calls[-1]]
    client=SimpleNamespace(peer=Peer,session=Session)
    native=ModuleType('honcho');native.Honcho=lambda **kwargs:client
    api=ModuleType('honcho.api_types');api.SessionPeerConfig=SimpleNamespace
    plugin=ModuleType('plugins.memory.honcho.client');plugin.HonchoClientConfig=SimpleNamespace(
        from_global_config=lambda:SimpleNamespace(user_observe_me=True,user_observe_others=False,
                                                 ai_observe_me=False,ai_observe_others=True))
    for name,value in [('honcho',native),('honcho.api_types',api)]:
        monkeypatch.setitem(sys.modules,name,value)
    def resolve(name,submodule=None):
        assert (name,submodule)==('honcho','client')
        return plugin
    monkeypatch.setattr('pacbench.provider_loader.provider_module',resolve)
    monkeypatch.setenv(enrollment.ENVIRONMENT,enrollment.PROTOCOL)
    monkeypatch.delenv('PACBENCH_STOP_FILE',raising=False)
    def read(root,workspace,plans):
        sessions={p['session_id'] for p in plans}
        return {'db_at':state['db_at'],'memberships':deepcopy([
            r for r in state['memberships'] if r['session_id'] in sessions])}
    monkeypatch.setattr(enrollment,'native_snapshot',read)
    monkeypatch.setattr('provider_adapters.honcho_clock.enabled',lambda root:False)
    return source,root,state,calls


def test_native_enrollment_saves_windows_and_preserves_original_source(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch)
    original=(source/'messages.json').read_bytes()
    result=enrollment.enroll_source(source,root)
    assert result['state']=='enrolled' and result['source_messages_submitted']==0
    assert calls==[['Sam','user','hermes']]
    assert (source/'messages.json').read_bytes()==original
    assert enrollment.verify_before_submission(root,source,[message()],HOST)==state
    state['memberships'][0]['left_at']=AFTER
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.verify_before_submission(root,source,[message()],HOST)


@pytest.mark.parametrize('now,joined,expected_calls',[(AFTER,BEFORE,0),(BEFORE,AFTER,1)])
def test_late_clock_and_enrollment_race_never_rewrite_rows_or_submit_messages(tmp_path,monkeypatch,now,joined,expected_calls):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch,now=now,joined=joined)
    with pytest.raises(enrollment.EnrollmentError):enrollment.enroll_source(source,root)
    assert len(calls)==expected_calls
    receipt=json.loads(enrollment.receipt_path(root,source).read_text())
    assert receipt['state']=='failed' and receipt['source_messages_submitted']==0
    if calls:assert state['memberships'][0]['joined_at']==AFTER


def test_cross_batch_roster_change_stops_before_native_writes(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch)
    plan=enrollment.source_plans([message()],HOST)[0]
    (root/'honcho-enrollment-rosters.json').write_text(json.dumps({plan['session_id']:['Me','Sam','Maya']}))
    with pytest.raises(enrollment.EnrollmentError,match='across imported batches'):
        enrollment.enroll_source(source,root)
    assert calls==[]


def test_changed_source_cannot_reuse_membership_receipt(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch)
    enrollment.enroll_source(source,root)
    changed=message();changed['text']='Changed source';(source/'messages.json').write_text(json.dumps([changed]))
    monkeypatch.setattr(enrollment,'native_snapshot',lambda *a:pytest.fail('read after stale binding'))
    with pytest.raises(enrollment.EnrollmentError,match='changed or incomplete'):
        enrollment.verify_before_submission(root,source,[changed],HOST)


def test_read_only_native_snapshot_quotes_source_identifiers(tmp_path,monkeypatch):
    plan=enrollment.source_plans([message()],HOST)[0];calls=[]
    def run(args,**kwargs):
        calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps(snapshot(plan)))
    monkeypatch.setattr(enrollment.subprocess,'run',run)
    enrollment.native_snapshot(tmp_path,"workspace'quoted",[plan])
    sql=calls[0][-1]
    assert sql.startswith('SELECT ') and "workspace_name='workspace''quoted'" in sql
    assert not any(token in sql for token in ('UPDATE ','INSERT ','DELETE '))


@pytest.mark.parametrize('enrollment_ready',[False,True])
def test_controller_enrolls_before_clock_advance_and_message_worker(tmp_path,monkeypatch,enrollment_ready):
    from pacbench import document_ingestion as ingestion
    from tests.test_document_ingestion import message as source_message
    schedule=ingestion.prepare_sources(tmp_path,[source_message('one')],'America/New_York')
    source=tmp_path/'sources'/schedule[0]['id'];root=tmp_path/'arm';root.mkdir()
    (root/'native-processing-settings.json').write_text(json.dumps({
        'deriver':{'REPRESENTATION_BATCH_MAX_AGE_SECONDS':1800}}))
    calls=[]
    def run(args,**kwargs):
        if 'provider_adapters.honcho_enrollment' in args:
            calls.append('enroll')
            result={'protocol':enrollment.PROTOCOL,'ready':enrollment_ready}
            return SimpleNamespace(returncode=0 if enrollment_ready else 1,
                stdout=json.dumps(result),stderr='')
        calls.append('submit')
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ready':True}),stderr='')
    monkeypatch.setattr(ingestion.subprocess,'run',run)
    monkeypatch.setattr(ingestion,'snapshot',lambda roles:{'state':'captured','at':'now','calls':[]})
    monkeypatch.setattr('provider_adapters.honcho_clock.enabled',lambda root:True)
    monkeypatch.setattr('provider_adapters.honcho_clock.before_turn',lambda *a:calls.append('clock'))
    record=ingestion.ingest_day('honcho',source,root,{
        'PACBENCH_USAGE_ROLES':'{}',enrollment.ENVIRONMENT:enrollment.PROTOCOL},schedule[0]['at'],'America/New_York')
    assert calls==(['enroll','clock','submit'] if enrollment_ready else ['enroll'])
    assert record['ready'] is enrollment_ready
    assert record['membership_protocol']==enrollment.PROTOCOL


def test_existing_membership_is_reused_without_readding_or_rewinding(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch,now=AFTER)
    plan=enrollment.source_plans([message()],HOST)[0]
    state.update(snapshot(plan))
    result=enrollment.enroll_source(source,root)
    assert result['ready'] is True and calls==[]
    assert state['memberships'][0]['joined_at']==BEFORE


def test_publication_audit_requires_covering_native_membership_and_roster_continuity():
    from pacbench.document_audit import audit_honcho_receipt
    source=message();plan=enrollment.source_plans([source],HOST)[0]
    result={'membership_protocol':enrollment.PROTOCOL,'membership_witness':snapshot(plan),
        'clock_protocol':'honcho-wall-clock-v3','source_error_scope':'submitted-source-sessions-v1',
        'result':[{'session_id':plan['session_id'],'observer':{'peer_id':'hermes','observe_others':True},
            'messages':[{'id':'native-one','session_id':plan['session_id'],'peer_id':'Sam',
                'content':source['text'],'created_at':source['written_at'],
                'metadata':{k:v for k,v in source.items() if k!='text'}}]}],
        'processing_status':[{'claims':0,'pending':0,'embeddings':0,'errors':[]}],
        'message_witnesses':[{'id':'native-one','queue_id':1,'processed':True,'has_error':False}]}
    rosters={}
    assert audit_honcho_receipt([source],result,previous_rosters=rosters)==[]
    assert rosters[plan['session_id']]==['Me','Sam']
    rosters[plan['session_id']]=['Me','Sam','Maya']
    assert 'Honcho chronological source membership is unproven' in audit_honcho_receipt([source],result,previous_rosters=rosters)
    result['membership_witness']['memberships'][0]['joined_at']=AFTER
    assert 'Honcho chronological source membership is unproven' in audit_honcho_receipt([source],result)


def test_installed_native_sdk_enrollment_uses_only_membership_requests():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    python=root/'vendor/venv-hermes-isolated/bin/python'
    if not python.exists():pytest.skip('Installed Honcho SDK runtime unavailable')
    script=root/'tests/fixtures/honcho_enrollment_sdk.py'
    env={**os.environ,'PYTHONPATH':str(root)}
    proc=subprocess.run([str(python),str(script)],cwd=root,env=env,capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,proc.stderr
    result=json.loads(proc.stdout)
    assert result['sdk_version']=='2.9.0' and result['model_calls']==result['live_network_calls']==0
    assert result['result']=='enrolled' and result['source_messages_submitted']==0


def test_two_threads_enroll_in_chronological_order_before_nightly_delivery(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch)
    second=message();second.update(thread='later-thread',source_id='two',written_at='2026-09-10T22:30:00+00:00')
    original=json.dumps([second,message()])  # Input file order cannot move the clock backward.
    (source/'messages.json').write_text(original)
    advances=[]
    monkeypatch.setattr('provider_adapters.honcho_clock.enabled',lambda root:True)
    monkeypatch.setattr('provider_adapters.honcho_clock.load',lambda root:(
        SimpleNamespace(now=lambda:datetime.fromisoformat(state['db_at'])),{}))
    def advance(root,at,env):
        assert datetime.fromisoformat(at)>=datetime.fromisoformat(state['db_at'])
        advances.append(at);state['db_at']=at
    monkeypatch.setattr('provider_adapters.honcho_clock.before_turn',advance)
    result=enrollment.enroll_source(source,root)
    assert result['ready'] is True and len(calls)==2 and len(result['observations'])==2
    # The first membership is already within its safe prelude. It must not
    # manufacture a fresh five-second deadline from the current clock value.
    assert advances==['2026-09-10T22:25:00+00:00']
    assert (source/'messages.json').read_text()==original
    enrollment.verify_before_submission(root,source,[second,message()],HOST)


def test_native_peer_encoding_keeps_original_names_and_legacy_plans():
    source=message();source['sender']='Alex Reed';source['structured']['participants']=['Me','Alex Reed']
    original=deepcopy(source)
    current=enrollment.source_plans([source],HOST)[0]
    assert current['peer_ids']=={'Alex Reed':'Alex-Reed','Me':'user'}
    assert current['source_peers']==['Alex-Reed','user']
    legacy=enrollment.source_plans([source],HOST,protocol=enrollment.LEGACY_PROTOCOL)[0]
    assert legacy['source_peers']==['Alex Reed','user'] and 'peer_ids' not in legacy
    assert source==original


def test_native_peer_encoding_collision_across_threads_or_batches_is_refused():
    first=message();first['sender']='Alex Reed';first['structured']['participants']=['Me','Alex Reed']
    second=message();second.update(source_id='two',thread='other',sender='Alex-Reed')
    second['structured']['participants']=['Me','Alex-Reed']
    with pytest.raises(enrollment.EnrollmentError,match='collide'):
        enrollment.source_plans([first,second],HOST)
    prior=enrollment.validate_peer_identities(enrollment.source_plans([first],HOST))
    with pytest.raises(enrollment.EnrollmentError,match='collide'):
        enrollment.validate_peer_identities(enrollment.source_plans([second],HOST),prior)


def test_encoded_source_acknowledgments_still_require_original_sender_metadata():
    from pacbench.document_audit import audit_honcho_receipt
    source=message();source['sender']='Alex Reed';source['structured']['participants']=['Me','Alex Reed']
    plan=enrollment.source_plans([source],HOST)[0]
    result={'membership_protocol':enrollment.PROTOCOL,'membership_witness':snapshot(plan),
        'clock_protocol':'honcho-wall-clock-v3','source_error_scope':'submitted-source-sessions-v1',
        'result':[{'session_id':plan['session_id'],'observer':{'peer_id':'hermes','observe_others':True},
            'messages':[{'id':'native-one','session_id':plan['session_id'],'peer_id':'Alex-Reed',
                'content':source['text'],'created_at':source['written_at'],
                'metadata':{k:v for k,v in source.items() if k!='text'}}]}],
        'processing_status':[{'claims':0,'pending':0,'embeddings':0,'errors':[]}],
        'message_witnesses':[{'id':'native-one','queue_id':1,'processed':True,'has_error':False}]}
    assert audit_honcho_receipt([source],result)==[]
    result['result'][0]['messages'][0]['metadata']['sender']='Alex-Reed'
    assert 'Honcho acknowledged changed source text, speaker, time or metadata' in audit_honcho_receipt([source],result)


def test_completed_enrollment_can_be_reused_only_after_native_revalidation(tmp_path,monkeypatch):
    source,root,state,calls=native_fixture(tmp_path,monkeypatch)
    enrollment.enroll_source(source,root)
    saved=enrollment.receipt_path(root,source).read_bytes()
    result=enrollment.enroll_source(source,root)
    assert result['ready'] and 'reuse_verified_native_snapshot' in result
    assert len(calls)==1 and enrollment.receipt_path(root,source).read_bytes()==saved
    state['memberships'][0]['left_at']=AFTER
    with pytest.raises(enrollment.EnrollmentError):enrollment.enroll_source(source,root)
    assert len(calls)==1
