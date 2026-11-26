"""Offline source/compiler fidelity. Stubs never establish semantic source quality."""
import copy
import json
import re
from dataclasses import replace
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench.corpus.authoring import (AuthoringError, ScenePlan, build_scene_observations,
    judge_scene, realize_scene, scene_prompt, scene_spec)
from pacbench.corpus.prepare import (WEEKDAYS, normalize_week, realization_binding, resolve_spec, reuse_realization)
from pacbench.followups import digest, save
from pacbench.corpus.audit import audit_scenes


def week(rows):
    days={d:{} for d in WEEKDAYS}
    days['monday']['scenes']=rows
    days['sunday']['conversations']=[{'id':'ask','time':'9 am','turns':[
        {'id':'question','prompt':'What are our plans?','expect':'Report only agreed plans.'}]}]
    return {'id':'local-authoring','week_start':'2026-10-05','timezone':'America/New_York','days':days}


def exact(**extra):
    return {'id':'one','thread':'friends','time':'6 pm',
        'messages':[{'sender':'Sam','text':'Hi there.'},{'sender':'Me','text':'Hello!'}],**extra}


def forbidden(*a,**kw):
    pytest.fail('Unexpected writer/checker/model call')


def built(plan):
    return [o.public() for o in build_scene_observations('local-authoring',plan,
        [(m['sender'],m['text']) for m in plan.messages])]


@pytest.mark.parametrize('shape', ['days', 'weeks'])
def test_calendar_spec_keeps_explicit_source_only_target(tmp_path, shape):
    spec = week([exact(), {
        'id': 'two', 'thread': 'friends', 'time': '7 pm',
        'scene': 'Two friends make lunch plans.',
        'facts': ['They agree to meet Friday at noon.'],
    }])
    prefix = ''
    if shape == 'weeks':
        spec['weeks'] = [spec.pop('days')]
        prefix = 'w1-'
    assert resolve_spec(spec, tmp_path)[1].id == prefix + 'two'

    spec['test_scene_id'] = prefix + 'one'
    _, target, resolved = resolve_spec(spec, tmp_path)
    assert target.id == resolved['test_scene_id'] == prefix + 'one'
    assert target.source_review == 'author-source-v1'
    assert resolved['checks'][0]['expect'] == 'Report only agreed plans.'

    spec['test_scene_id'] = prefix + 'missing'
    with pytest.raises(ValueError, match='test_scene_id does not identify a scene'):
        resolve_spec(spec, tmp_path)


def test_flat_spec_with_checks_can_have_multiple_fact_bearing_scenes(tmp_path):
    spec = normalize_week(week([
        exact(facts=['Sam says hello.']),
        exact(id='two', time='7 pm', facts=['Sam checks in.'],
              messages=[{'sender': 'Sam', 'text': 'How is it going?'}]),
        exact(id='three', time='8 pm', messages=[{'sender': 'Sam', 'text': 'Good night.'}]),
    ]))
    del spec['test_scene_id']
    _, target, resolved = resolve_spec(spec, tmp_path)
    assert target.id == resolved['test_scene_id'] == 'three'
    assert resolved['checks'][0]['expect'] == 'Report only agreed plans.'

    del spec['checks']
    with pytest.raises(ValueError, match='choose one test scene'):
        resolve_spec(spec, tmp_path)


def test_exact_appearance_silent_members_and_later_generated_roster(tmp_path):
    spec=week([exact(participants=['Me','Sam','Silent'],messages=[
        {'sender':'Sam','text':'Hello.'},{'sender':'Nora','text':'Hi everyone.'},
        {'sender':'Me','text':'Welcome.'}]),
        {'id':'two','thread':'friends','time':'7 pm','scene':'Sam and I discuss music.','facts':[]}])
    plans,_,resolved=resolve_spec(spec,tmp_path)
    messages=built(plans[0])
    assert [m['structured']['participants'] for m in messages]==[
        ['Me','Sam','Silent'],['Me','Sam','Silent','Nora'],['Me','Sam','Silent','Nora']]
    assert plans[1].participants==('Me','Sam','Silent','Nora')
    assert 'Nora' not in messages[0]['structured']['participants']
    assert len({m['thread'] for m in messages})==1
    assert 'Nora' in scene_prompt(plans[1]) and 'Silent' in scene_prompt(plans[1])
    with pytest.raises(AuthoringError,match='Unknown source participant'):
        build_scene_observations('life',plans[1],[('Invented','hello'),('Me','Hi')])
    from pacbench.document_ingestion import document_text
    text=document_text(messages,messages[-1]['collected_at'])
    old,new=text.split('Participants: Me, Sam, Silent, Nora')
    assert 'Nora' not in old
    assert len(re.findall(r'Conversation: imessage \| '+messages[0]['thread'],text))==2


def test_first_speaker_not_future_speaker_and_explicit_silent_join(tmp_path):
    plans,_,_=resolve_spec(week([exact(messages=[{'sender':'Sam','text':'Hi'},
        {'sender':'Nora','text':'Hello'}]),exact(id='two',time='7 pm',participants=['Me','Late'],
        messages=[{'sender':'Me','text':'Nice mural.'}])]),tmp_path)
    assert built(plans[0])[0]['structured']['participants']==['Me','Sam']
    assert built(plans[1])[0]['structured']['participants']==['Me','Sam','Nora','Late']
    with pytest.raises(AuthoringError,match='participants'):
        resolve_spec(week([exact(messages=[{'sender':'Me','text':'Hi'}, {'sender':'Sam','text':'Later'}])]),tmp_path)


@pytest.mark.parametrize('change', ['missing-generated-roster','wrong-order','type-change','subject-change','cast-only','unknown-field'])
def test_thread_metadata_fails_before_models(tmp_path,change):
    spec=week([exact(),exact(id='two',time='7 pm')])
    rows=spec['days']['monday']['scenes']
    if change=='missing-generated-roster':rows[0]={'id':'one','thread':'new','time':'6 pm','scene':'They chat.','facts':[]}
    if change=='wrong-order':rows.reverse()
    if change=='type-change':rows[1]['type']='email'
    if change=='subject-change':rows[1]['subject']='Another subject'
    if change=='cast-only':spec['cast']={'Nobody':'A silent person.'}
    if change=='unknown-field':rows[0]['messages'][0]['audience']=['Sam']
    with pytest.raises((AuthoringError,ValueError,TypeError)):resolve_spec(spec,tmp_path)


def test_defaults_mixed_overrides_preserve_text_and_chronology(tmp_path):
    row=exact(messages=[{'sender':'Sam','text':'  Hi 🍕\n  '},
        {'sender':'Me','text':'Hello','time':'6:03:07 pm'},
        {'sender':'Sam','text':'Thanks'},
        {'sender':'Me','text':'Fine','at':'2026-10-05T18:08:02-04:00'},
        {'sender':'Sam','text':'Bye'}])
    plan=resolve_spec(week([row]),tmp_path)[0][0]
    assert [m['at'][11:19] for m in plan.messages]==['18:00:00','18:03:07','18:04:07','18:08:02','18:09:02']
    assert built(plan)[0]['text']=='  Hi 🍕\n  '
    assert plan.source_review=='author-source-v1'
    for override in ['5 pm','6 pm','6:30 pm','bad']:
        bad=copy.deepcopy(row);bad['messages'][1]['time']=override
        with pytest.raises((AuthoringError,ValueError)):resolve_spec(week([bad]),tmp_path)


def test_midnight_and_dst_elapsed_defaults_and_readable_rejection():
    def plan(start,end,messages):
        return ScenePlan(id='clock',channel='imessage',participants=('Me','Sam'),
            at=start,window_end=end,timezone='America/New_York',messages=messages,
            source_review='author-source-v1')
    two=({'sender':'Sam','text':'Hi'},{'sender':'Me','text':'Hello'})
    p=plan('2026-10-05T23:59:00-04:00','2026-10-06T00:10:00-04:00',two)
    assert p.messages[1]['at']=='2026-10-06T00:00:00-04:00'
    p=plan('2026-11-01T01:59:00-04:00','2026-11-01T01:10:00-05:00',two)
    assert p.messages[1]['at']=='2026-11-01T01:00:00-05:00'
    p=plan('2026-03-08T01:59:00-05:00','2026-03-08T03:10:00-04:00',two)
    assert p.messages[1]['at']=='2026-03-08T03:00:00-04:00'
    for start,end,time in [('2026-11-01T00:59:00-04:00','2026-11-01T02:10:00-05:00','1:30 am'),
        ('2026-03-08T01:59:00-05:00','2026-03-08T03:10:00-04:00','2:30 am')]:
        with pytest.raises(ValueError,match='ambiguous|nonexistent'):
            plan(start,end,({'sender':'Sam','text':'Hi','time':time},))
    with pytest.raises(AuthoringError,match='outside'):
        plan('2026-10-05T23:59:00-04:00','2026-10-06T00:10:00-04:00',
            ({'sender':'Sam','text':'Hi','time':'12:01 am'},))


def test_unjudged_realize_cache_audit_and_forged_receipt(tmp_path):
    plan=resolve_spec(week([exact()]),tmp_path)[0][0]
    result=realize_scene(plan,forbidden,forbidden,record_dir=tmp_path/'checks')
    assert result.record.attempts==result.record.judge_calls==0
    assert result.verdict=={'state':'unjudged','protocol':'author-source-v1','integrity':'pass'}
    assert not (tmp_path/'checks').exists()
    row={'scene_id':plan.id,'lines':[list(x) for x in result.lines],
        'source_ids':[m['source_id'] for m in built(plan)],'record':vars(result.record),
        'verdict':result.verdict,'verification_binding':realization_binding(plan,result.lines,())}
    save(tmp_path/'scenes.json',{'scenes':[row]})
    resolved={'scenes':[scene_spec(plan)]}
    assert audit_scenes(tmp_path,resolved,'local-authoring',built(plan))==[]
    assert reuse_realization(plan,row,forbidden,model='offline',record_dir=tmp_path,verification_lines=()).verdict==result.verdict
    with pytest.raises(AuthoringError,match='unjudged'):judge_scene(plan,list(result.lines),forbidden)
    for kind in ['model-pass','fake-path','edited-line','edited-time']:
        bad=copy.deepcopy(row)
        if kind=='model-pass':bad['verdict']={'supported':True}
        if kind=='fake-path':bad['record']['judgment_paths']=['fake.json']
        if kind=='edited-line':bad['lines'][0][1]='fabricated'
        if kind=='edited-time':bad['verification_binding']='invented'
        with pytest.raises((ValueError,AuthoringError)):
            reuse_realization(plan,bad,forbidden,model='offline',record_dir=tmp_path,verification_lines=())
        save(tmp_path/'scenes.json',{'scenes':[bad]})
        if kind=='edited-line':
            with pytest.raises(AuthoringError):audit_scenes(tmp_path,resolved,'local-authoring',built(plan))
        else:assert audit_scenes(tmp_path,resolved,'local-authoring',built(plan))
    with pytest.raises(AuthoringError,match='without scene facts'):
        replace(plan,facts=('An existing semantic contract.',))


def test_apple_fixture_fails_closed_without_rewriting_snapshot(tmp_path):
    from pacbench.imessage_fixture import append_messages
    first=resolve_spec(week([exact()]),tmp_path)[0][0]
    later=resolve_spec(week([exact(),exact(id='two',time='7 pm',messages=[
        {'sender':'Sam','text':'Hello.'},{'sender':'Nora','text':'Hi folks.'}])]),tmp_path)[0][1]
    db=tmp_path/'chat.db';append_messages(db,built(first))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError,match='global chat_handle_join'):
        append_messages(db,built(later))
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    with pytest.raises(ValueError,match='historical membership'):
        append_messages(tmp_path/'new.db',built(later))


def test_email_rosters_change_per_message_without_changing_thread(tmp_path):
    from pacbench.email_fixture import append_emails
    plan=resolve_spec(week([exact(type='email',subject='Our mural',messages=[
        {'sender':'Sam','text':'Hello'},{'sender':'Nora','text':'Hi both'}])]),tmp_path)[0][0]
    msgs=built(plan);p=tmp_path/'mail.json';append_emails(p,msgs)
    rows=json.loads(p.read_text())['messages'];first,second=[BytesParser(policy=policy.default).parsebytes(r['rfc822'].encode()) for r in rows]
    assert 'Nora' not in str(first['To']) and 'Nora' not in str(first['From'])
    assert 'Nora' in str(second['From']) and first['Message-ID'] in str(second['References'])
    assert msgs[0]['thread']==msgs[1]['thread']


def test_draft_uses_default_clock_without_changing_questions_or_jen_blend(tmp_path):
    new=json.loads(Path('specs/pizza_week_assistant.json').read_text())
    np,_,nrr=resolve_spec(new,tmp_path)
    catalog_plans,_,catalog=resolve_spec(json.loads(
        Path('specs/pizza_week_catalog.json').read_text()),tmp_path)
    by_id={r['id']:r for r in nrr['checks']}
    assert len(by_id)==42 and len(catalog['checks'])==37
    assert all(by_id[r['id']]==r for r in catalog['checks'])
    revised={p.id:p for p in np}
    speaker_text=lambda p: [(m['sender'],m['text']) for m in p.messages]
    for p in catalog_plans:
        if p.messages is not None:
            assert speaker_text(p)==speaker_text(revised[p.id])
    for w in new['weeks']:
        for d in w.values():
            for scene in d.get('scenes',[]):
                assert not {'message_range','messages_per_person','size_range'} & scene.keys()
                for msg in scene.get('messages',[]):
                    assert set(msg)=={'sender','text'}
    for p in np:
        if p.messages is None:
            continue
        assert p.source_review=='author-source-v1'
        clocks=[datetime.fromisoformat(m['at']) for m in p.messages]
        assert clocks[0]==datetime.fromisoformat(p.at)
        assert all((b-a).total_seconds()==60 for a,b in zip(clocks,clocks[1:]))
    invitation=revised['w1-jen-matinee-invite'];followup=revised['w1-jen-matinee-followup']
    assert len(invitation.messages)==1 and invitation.messages[0]['sender']=='Jen'
    assert invitation.thread==followup.thread and followup.messages is None
    assert 'has not accepted' in ' '.join(followup.facts)
    assert 'channels' not in new and 'cast' not in new
    oldmsgs=built(revised['w1-neighbors-start']);join=built(revised['w1-neighbors-new-speaker'])
    later=built(revised['w1-neighbors-later'])
    assert all('Nora' not in m['structured']['participants'] for m in oldmsgs)
    assert 'Nora' in join[0]['structured']['participants'] and 'Nora' in later[0]['structured']['participants']


def test_guide_mixed_example_compiles_offline(tmp_path):
    text=Path('docs/authoring-options.md').read_text()
    example=json.loads(re.search(r'```json\n(.*?)\n```',text,re.S)[1])
    plans,_,_=resolve_spec(example,tmp_path)
    assert len(plans)==3 and plans[-1].participants==('Me','Maya','Leo','Sam')


@pytest.mark.parametrize('imported', [False, True])
def test_prepare_new_exact_generated_cache_portable_audit_with_stubs(tmp_path,monkeypatch,imported):
    import pacbench.corpus.prepare as module
    from pacbench.corpus.audit import audit_prepared
    from pacbench.corpus.prepare import prepare,export_week,import_week
    from tests.test_supplied_messages import supplied_spec,checker,fake_contract
    root=tmp_path/'prepared';spec=supplied_spec();row=spec['scenes'][0]
    row.pop('facts');row['test_intent']='Recall the lunch invitation.'
    spec['test_scene_id']='exact';spec['scenes'].append({'id':'generated','thread':'sam',
        'channel':'imessage','participants':['Me','Sam'],'at':'2026-10-06T18:00:00-04:00',
        'scene':'Sam and I chat about a movie.','facts':[]})
    calls=[];writer=[]
    def write(prompt):
        writer.append(prompt)
        return json.dumps({'dialogue':'Sam: Nice film.\nMe: I liked it.','writer_note':'Short movie chat.'})
    monkeypatch.setenv('META_API_KEY','offline-test')
    monkeypatch.setattr(module,'load_driver',lambda:SimpleNamespace(model_id='offline-stub'))
    monkeypatch.setattr(module,'ResponsesWriter',lambda **kw:write)
    monkeypatch.setattr(module,'ModelClient',lambda **kw:checker(root,calls,model='offline-stub'))
    monkeypatch.setattr(module,'client_settings',lambda *a:('http://offline.test','offline'))
    monkeypatch.setattr(module,'build_contract',fake_contract)
    kwargs={}
    if imported:
        plans=resolve_spec(spec,tmp_path)[0]
        kwargs['imported_scenes']={'source_bundle_hash':'offline-frozen-source', 'scenes':{
            'exact':[[m['sender'],m['text']] for m in plans[0].messages],
            'generated':[['Sam','Nice film.'],['Me','I liked it.']]}}
    result=prepare(spec,root,**kwargs)
    assert result['state']=='prepared' and audit_prepared(root)['state']=='pass'
    from pacbench.native_pilot import read_prepared
    received,_,_=read_prepared(root,100)
    assert all('source_review' not in m and 'facts' not in m for m in received)
    rows=json.loads((root/'scenes.json').read_text())['scenes']
    assert rows[0]['verdict']['state']=='unjudged' and rows[0]['record']['judge_calls']==0
    assert rows[1]['record']['judge_calls']==1
    assert len(writer)==(0 if imported else 1)
    assert len(calls)==2  # Generated scene only; independent corpus/question audit.
    assert not any('supplied source' in str(c) for c in calls)
    assert 'unjudged author source' not in (root/'bundle.json').read_text()
    assert 'author-source' in json.loads((root/'bundle-provenance.json').read_text())['source']
    corpus=json.loads((root/'realism-review.json').read_text())
    assert corpus['review_version']=='source-realism-v11'
    assert 'Author-owned exact source IDs:' in calls[-1]
    assert rows[0]['source_ids'][0] in calls[-1]
    export_week(root,root/'portable.json')
    import_week(root/'portable.json',tmp_path/'restored')
    assert audit_prepared(tmp_path/'restored')['state']=='pass'
    assert read_prepared(tmp_path/'restored',100)[0]==received
    if not imported:
        again=prepare(spec,root,resume=True)
        assert again['state']=='prepared' and audit_prepared(root)['state']=='pass'
        assert len(writer)==1


def test_legacy_review_cannot_be_dropped_on_resume_before_writes(tmp_path,monkeypatch):
    from pacbench.corpus.prepare import prepare
    from tests.test_supplied_messages import supplied_spec
    root=tmp_path/'old';root.mkdir();old=supplied_spec();_,_,resolved=resolve_spec(old,root)
    save(root/'resolved-spec.json',resolved)
    before=(root/'resolved-spec.json').read_bytes()
    old['scenes'][0].pop('facts')
    old['scenes'][0]['test_intent']='Recall the plan.'
    with pytest.raises(ValueError,match='Cannot drop a legacy scene review'):
        prepare(old,root,resume=True)
    assert (root/'resolved-spec.json').read_bytes()==before
    assert not (root/'spec.json').exists()


def test_explicit_silent_join_additive_rosters_and_stable_history(tmp_path):
    rows=[exact(participants=['Me','Sam','Robin']),
        exact(id='two',time='7 pm',joins=['Nora'],messages=[{'sender':'Sam','text':'Nice mural.'}]),
        exact(id='three',time='8 pm',participants=['Me','Sam'],messages=[{'sender':'Me','text':'Lovely colors.'}])]
    plans=resolve_spec(week(rows),tmp_path)[0]
    before=built(plans[0]);second=built(plans[1]);third=built(plans[2])
    assert before[0]['structured']['participants']==['Me','Sam','Robin']
    assert second[0]['structured']['participants']==third[0]['structured']['participants']==['Me','Sam','Robin','Nora']
    assert built(plans[0])==before
    assert len({m['thread'] for m in before+second+third})==1
    from pacbench.imessage_fixture import append_messages
    db=tmp_path/'chat.db';append_messages(db,before)
    with pytest.raises(ValueError,match='historical membership'):append_messages(db,second)
    # Generated source can declare the same silent addition before generation.
    rows[1]={'id':'two','thread':'friends','time':'7 pm','joins':['Nora'],
             'scene':'Sam and I discuss a mural.','facts':[]}
    generated=resolve_spec(week(rows),tmp_path)[0][1]
    assert generated.participants==('Me','Sam','Robin','Nora')
    assert 'Nora' in scene_prompt(generated)


@pytest.mark.parametrize('joins',[None,'Nora',[1],[''],[' Nora '],['Nora','Nora'],['Me'],['Sam']])
def test_invalid_explicit_joins_fail_offline(tmp_path,joins):
    with pytest.raises(ValueError,match='joins'):
        resolve_spec(week([exact(),exact(id='two',time='7 pm',joins=joins)]),tmp_path)


def test_visible_names_are_not_linked_by_nickname_evidence(tmp_path):
    from pacbench.email_fixture import append_emails,mail_address
    from pacbench.imessage_fixture import identity
    plans=resolve_spec(week([
        exact(thread='alex-text',messages=[{'sender':'Alex','text':'How is the mural?'}]),
        exact(id='email',thread='alice-email',type='email',subject='The mural',time='7 pm',
              messages=[{'sender':'Alice','text':'Alex is my nickname. I liked the mural too.'}]),
        exact(id='same-label',thread='alex-email',type='email',subject='Paint',time='8 pm',
              messages=[{'sender':'Alex','text':'Those colors are great.'}])]),tmp_path)[0]
    public=[m for p in plans for m in built(p)]
    assert [m['sender'] for m in public]==['Alex','Alice','Alex']
    assert [p.participants for p in plans]==[('Me','Alex'),('Me','Alice'),('Me','Alex')]
    assert public[1]['text']=='Alex is my nickname. I liked the mural too.'
    assert identity('Alex')['uid']!=identity('Alice')['uid']
    assert mail_address('Alex')==identity('Alex')['uid'].lower()+'@mail.example.test'
    assert mail_address('Alice')!=mail_address('Alex')
    p=tmp_path/'mail.json';append_emails(p,public[1:])
    rendered=json.loads(p.read_text())['messages']
    assert mail_address('Alice') in rendered[0]['rfc822']
    assert mail_address('Alex') in rendered[1]['rfc822']
    assert not any('alias' in key or 'identity' in key for m in public for key in (m.get('structured') or {}))


def test_latest_refinement_preserves_all_questions_messages_and_clean_timing(tmp_path):
    # Fingerprints from specs/pizza_week_assistant.json at 51b2d8dda35c2a96c0b163d0a144d3609c6983dd.
    retained_ids = '''
        draft-only preference-old namesake-reed visit-plan games-standing workshop-plan
        jen-open appointments lunch-invite evening-control ben-original neighbors-start
        leo-delayed studio-clarifies lunch-time lunch-relay lunch-venue jen-answer hall-expansion
        tickets-invite namesake-chen roster-addition leo-withdraws ben-replaces fresh-time-old-quote
        jen-delivery preference-correction concert-revealed concert-companion link-neighbor
        unrelated-pizza personal-commitment jen-matinee-invite complete-roster ben-declined
        ben-distinct-mail attendance-evidence game-context link-arrives date-history neighbors-new-speaker
        proofreading-done visit-excursion low-priority-real-update old-preference-quote
        jen-matinee-followup neighbors-later visit-end-change series-ends small-future-plan
        future-pizza pending-invite quiet-chatter
    '''.split()
    spec_path = Path(__file__).resolve().parents[1] / 'specs/pizza_week_assistant.json'
    spec = json.loads(spec_path.read_text())
    scenes = {scene['id']: scene for week in spec['weeks'] for day in week.values()
              for scene in day.get('scenes', [])}
    retained = {scene_id: {field: scenes[scene_id].get(field) for field in ('messages', 'time', 'message_range')}
                for scene_id in retained_ids}
    assert digest(retained) == '9adb7870453df887b00a299f3a58a9bf8f0e0cf59fd3e5727997183c1b8d5e16'
    assert digest(resolve_spec(spec, tmp_path)[2]['checks']) == '11faf03e1ad9a1ec949c01d771eae41f58ed845cbb2463dc6202a3ced05cc54d'
