"""Offline authoring integrity; these tests do not measure provider accuracy."""
import json
from types import SimpleNamespace
from datetime import datetime
import pytest
from pacbench.prepare import resolve_spec, prepare
from pacbench.harness import _summarize_tool_result
from pacbench.authoring import ScenePlan, judge_prompt, scene_prompt, spread_timestamps

def test_corpus_review_temporal_instruction_and_rejection_path(tmp_path):
    from pacbench.corpus_review import review_corpus, review_prompt
    resolved={'scenes': [], 'checks': []}
    base={'channel':'email','thread':'ben-email','sender':'Ben'}
    original=dict(base, source_id='original', written_at='2026-10-12T20:00:00-04:00',
                  text='Are we still good for 7pm next Thursday, Oct 22?')
    # Actual wording from the frozen October 13 reply; it lacks an anchor for Saturday.
    reply=dict(base, source_id='reply', written_at='2026-10-13T10:00:00-04:00',
               text='A work deadline means I cannot get away Thursday evening — could we move dinner to Saturday instead?')
    clear=dict(reply, text='Could we move dinner from Thursday October 22 to Saturday October 24 instead?')
    for m in (original, reply, clear):
        m['collected_at']=m['written_at']; m['from_owner']=False
    for candidate in (reply, clear):
        prompt=review_prompt([original,candidate],resolved,version='source-realism-v5')
        assert candidate['text'] in prompt and candidate['written_at'] in prompt
        assert 'message containing it' in prompt and 'do not invent an anchor' in prompt
    def verdict(consistent):
        return SimpleNamespace(text=json.dumps({'consistent':consistent,'expectations_supported':consistent,
            'natural_enough':True,'questions_answerable':consistent,'issues':[] if consistent else ['Saturday date ambiguous'], 'assessments':[]}),
            record_path='receipt.json',usage={})
    with pytest.raises(ValueError, match='Corpus realism review rejected'):
        review_corpus([original,reply],resolved,lambda _: verdict(False),tmp_path)
    assert json.loads((tmp_path/'realism-review.json').read_text())['state']=='fail'
    assert review_corpus([original,clear],resolved,lambda _: verdict(True),tmp_path)['state']=='pass'

def test_saved_scene_bounds_compare_equal_for_resume():
    from pacbench.authoring import scene_spec
    plan=ScenePlan(scene='A chat',participants=('Me','Sam'),
                   message_range=(4,12),messages_per_person=(2,6))
    before=scene_spec(plan)
    assert json.loads(json.dumps(before))==before

def test_email_checker_can_verify_cross_channel_reference_without_writer_recap():
    from pacbench.authoring import realize_scene,build_scene_observations
    plan=ScenePlan(scene='Ben moves dinner over email.',channel='email',thread='ben-email',
                   participants=('Me','Ben'),email_subject='Dinner',message_range=(2,4))
    prior=(('2026-10-05T18:00:00-04:00','imessage:ben','Ben','Dinner Thursday at Juniper?'),)
    def writer(prompt):
        assert 'Dinner Thursday at Juniper?' not in prompt
        assert 'Subject: Dinner' in prompt
        return 'Ben: Can we move dinner to Saturday?\nMe: Saturday works.'
    def checker(prompt):
        assert '[imessage:ben] Ben: Dinner Thursday at Juniper?' in prompt
        return json.dumps({'supported':True,'evidence':[1,2],'problem':''})
    result=realize_scene(plan,writer,checker,verification_lines=prior)
    messages=build_scene_observations('email-week',plan,list(result.lines))
    assert messages[0].structured['email_subject']=='Dinner'


def test_email_reply_quotes_survive_as_one_body_with_the_actual_sender(tmp_path):
    from email import message_from_string
    from email.policy import default
    from pacbench.authoring import realize_scene,build_scene_observations
    from pacbench.email_fixture import append_emails
    plan=ScenePlan(scene='Ben moves dinner to Saturday, quoting the old Thursday plan.',
        channel='email',thread='dinner',participants=('Me','Ben'),email_subject='Dinner',message_range=(2,4))
    body='Saturday works at seven.\n\nOn Monday Ben wrote:\n> Thursday at seven?\n> Me: sounds good'
    def writer(_):return json.dumps({'dialogue':[{'sender':'Ben','body':'Can we do Saturday instead?'},
        {'sender':'Me','body':body}],'writer_note':'A reply retains the quoted old arrangement.'})
    def checker(_):return json.dumps({'supported':True,'evidence':[1,2],'problem':''})
    result=realize_scene(plan,writer,checker)
    messages=[m.public() for m in build_scene_observations('email-quotes',plan,list(result.lines))]
    mailbox=tmp_path/'mailbox.json';append_emails(mailbox,messages)
    rows=json.loads(mailbox.read_text())['messages']
    assert len(rows)==2 and messages[1]['from_owner'] is True
    assert messages[1]['text']==body
    parsed=message_from_string(rows[1]['rfc822'],policy=default)
    assert parsed.get_content().strip().splitlines()==body.splitlines()
    assert parsed['In-Reply-To']==rows[0]['message_id']


def test_noisy_thread_context_does_not_discard_the_original_plan():
    plan=ScenePlan(scene='Move the existing pizza to five.',participants=('Me','Maya'))
    prior=(('2026-10-12T10:00:00-04:00','Maya',"Friday at Luigi's at six?"),)+tuple(
        ('2026-10-12T11:00:00-04:00','Me',f'Unrelated message {i}') for i in range(40))
    assert "Friday at Luigi's at six?" in scene_prompt(plan,prior)
    checker_prior=tuple((at,'imessage:maya',who,text) for at,who,text in prior)
    assert "Friday at Luigi's at six?" in judge_prompt(plan,[('Maya','Can we do five instead?'),('Me','Yes')],checker_prior)


def test_group_noise_allows_a_speaker_to_exceed_their_target_share():
    from pacbench.authoring import realize_scene
    plan=ScenePlan(scene='Friends catching up.',participants=('Me','Sam','Jordan'),
                   message_range=(6,9),messages_per_person=(2,3))
    lines=[(name,f'Message {i} from {name}') for name,n in [('Me',4),('Sam',2),('Jordan',2)] for i in range(n)]
    writer=lambda _: '\n'.join(f'{s}: {t}' for s,t in lines)
    checker=lambda _: json.dumps({'supported':True,'evidence':[1,8],'problem':''})
    result=realize_scene(plan,writer,checker)
    assert result.lines==tuple(lines) and result.record.attempts==1


def test_group_noise_still_requires_each_member_minimum_and_a_bounded_total():
    from pacbench.authoring import realize_scene,AuthoringError
    plan=ScenePlan(scene='Friends catching up.',participants=('Me','Sam','Jordan'),
                   message_range=(6,9),messages_per_person=(2,3))
    for counts in ((5,2,1),(4,3,3)):
        lines=[(name,f'Message {i} from {name}') for name,n in zip(plan.participants,counts) for i in range(n)]
        writer=lambda _: '\n'.join(f'{s}: {t}' for s,t in lines)
        with pytest.raises(AuthoringError,match='member minimum|message count'):
            realize_scene(plan,writer,lambda _: pytest.fail('Out-of-bounds text reached the checker'))

def test_writer_and_verifier_see_the_local_message_window():
    plan = ScenePlan(scene="Make an evening plan.",
                     at="2026-09-08T19:00:00", id="evening")
    assert "between 19:00 and 19:20 local time" in scene_prompt(plan)
    assert "between 19:00 and 19:20 local time" in judge_prompt(
        plan, [("Me", "Today is dragging")])


def test_frozen_pacing_is_uneven_reproducible_and_preserves_dst_window():
    plan=ScenePlan(id='uneven-replies',scene='An ordinary chat.',
        at='2026-11-01T01:55:00-04:00',window_end='2026-11-01T01:15:00-05:00',
        timezone='America/New_York')
    stamps=spread_timestamps(plan,8)
    assert stamps==spread_timestamps(plan,8)
    assert stamps[0]==plan.window_start and stamps[-1]==plan.window_end
    parsed=[datetime.fromisoformat(v) for v in stamps]
    gaps=[(b-a).total_seconds() for a,b in zip(parsed,parsed[1:])]
    assert min(gaps)>0 and len(set(gaps))>1
    assert sum(gaps)==1200

def test_blocked_tool_result_is_not_successful():
    event = {"type": "tool_result", "tool": "mcp__hindsight__list_memories",
             "success": True,
             "result": {"error": "PACBENCH-BLOCKED: outside memory-tool boundary"}}
    result = _summarize_tool_result(event)
    assert result["success"] is False
    assert result["policy_blocked"] is True

def test_bridge_error_result_is_not_successful():
    result = _summarize_tool_result({"type": "tool_result", "tool": "tool_call",
                                     "success": True,
                                     "result": {"error": "Use one local tool per call"}})
    assert result["success"] is False
    assert result["policy_blocked"] is False

def test_invalid_spec_keeps_incomplete_experiment_artifact(tmp_path):
    root = tmp_path / "failed-run"
    spec = {"id": "bad", "scenes": [], "test_scene_id": "missing"}
    with pytest.raises(ValueError, match="at least one scene"):
        prepare(spec, root)
    status = json.loads((root / "experiment-status.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    assert status["state"] == "incomplete"
    assert status["stage"] == "spec validation"
    assert summary["failed_stage"] == "spec validation"
    assert json.loads((root / "spec.json").read_text()) == spec

def test_failed_preparation_audit_marks_body_incomplete(tmp_path, monkeypatch):
    import importlib
    experiment = importlib.import_module("pacbench.prepare")

    root = tmp_path / "audit-failed"
    monkeypatch.setattr(experiment, "_prepare_body",
                        lambda spec, path, progress: {"state": "prepared", "results": []})
    monkeypatch.setattr(experiment, "audit_prepared",
                        lambda path: {"state": "fail", "errors": ["duplicate input"]})
    summary = experiment.prepare({"id": "pizza"}, root)
    assert summary["state"] == "incomplete"
    assert json.loads((root / "run-audit.json").read_text())["errors"] == ["duplicate input"]
    assert json.loads((root / "experiment-status.json").read_text())["state"] == "incomplete"


def test_auth_failure_stops_later_independent_scenes_without_freezing(tmp_path,monkeypatch):
    import importlib
    from pacbench.gateway_client import ModelAccessBlocked
    module=importlib.import_module('pacbench.prepare')
    calls=[]
    def blocked(prompt):
        calls.append(prompt)
        raise ModelAccessBlocked('Upstream authentication failed')
    monkeypatch.setenv('META_API_KEY','test-key')
    monkeypatch.setattr(module,'load_driver',lambda:SimpleNamespace(model_id='test-model'))
    monkeypatch.setattr(module,'client_settings',lambda _:('http://127.0.0.1:8099/p/test/v1','test-key'))
    monkeypatch.setattr(module,'ResponsesWriter',lambda **_:blocked)
    monkeypatch.setattr(module,'ModelClient',lambda **_:blocked)
    spec={'id':'auth-stop','test_scene_id':'second','scenes':[
        {'id':'first','thread':'one','at':'2026-10-12T18:00:00','scene':'Make a plan.','facts':['They agree to pizza.']},
        {'id':'second','thread':'two','at':'2026-10-13T18:00:00','scene':'Make another plan.','facts':['They agree to coffee.']}]}
    root=tmp_path/'preparation'
    summary=module.prepare(spec,root)
    assert len(calls)==1
    assert summary['state']=='incomplete'
    assert summary['errors'][0]['scene_id']=='first'
    assert 'authentication' in summary['errors'][0]['reason']
    assert not (root/'week.json').exists()

def test_scene_clocks_and_ids_are_derived_from_one_occurrence_time(tmp_path):
    spec = {"id": "ice-cream", "scenes": [{
        "at": "2026-10-01T17:00:00", "scene": "Mike, Sarah and I get ice cream.",
        "facts": ["The meetup is at 5pm."]}]}
    scenes, target, resolved = resolve_spec(spec, tmp_path / "unused")
    assert target is scenes[0]
    assert scenes[0].id == "scene-01"
    assert scenes[0].thread == "scene-01"
    assert scenes[0].window_end == "2026-10-01T17:20:00-04:00"
    assert resolved["question_at"] == "2026-10-02T09:00:00-04:00"
    assert "target_lines" not in resolved["scenes"][0]
    assert "id" not in spec["scenes"][0]

def test_local_wall_time_stays_seven_pm_across_dst(tmp_path):
    spec = {"timezone": "America/New_York", "test_scene_id": "second",
            "scenes": [
                {"id": "first", "thread": "pizza", "at": "2026-10-31T19:00:00",
                 "scene": "Devon and Me make a plan."},
                {"id": "second", "thread": "pizza", "at": "2026-11-02T19:00:00",
                 "scene": "They update it.", "facts": ["The plan moved."]}]}
    scenes, _, resolved = resolve_spec(spec, tmp_path)
    assert scenes[0].at == "2026-10-31T19:00:00-04:00"
    assert scenes[1].at == "2026-11-02T19:00:00-05:00"
    assert datetime.fromisoformat(scenes[0].at).hour == 19
    assert datetime.fromisoformat(scenes[1].at).hour == 19
    assert resolved["question_at"] == "2026-11-03T09:00:00-05:00"
    assert spread_timestamps(scenes[0], 10)[-1] == scenes[0].window_end

def test_same_thread_writer_sees_prior_messages_without_recap_instruction(tmp_path):
    scenes, _, _ = resolve_spec({"test_scene_id": "second", "scenes": [
        {"id": "first", "thread": "pizza", "scene": "Make a pizza plan."},
        {"id": "second", "thread": "pizza", "scene": "Move it later.",
         "facts": ["They agree on a later time."]}]}, tmp_path)
    prompt = scene_prompt(scenes[1], (("2026-09-09T18:00:00-04:00",
                                       "Devon", "See you at Luigi's"),))
    assert "2026-09-09 Devon: See you at Luigi's" in prompt
    assert "do not recap settled details" in prompt
    assert "don't reply as if an old message just arrived" in prompt

def test_scene_order_and_same_thread_overlap_rejected(tmp_path):
    first = {"id": "first", "thread": "pizza", "at": "2026-09-10T18:00:00",
             "scene": "First message."}
    second = {"id": "second", "thread": "pizza", "at": "2026-09-10T17:00:00",
              "scene": "Second message.", "facts": ["A fact."]}
    with pytest.raises(ValueError, match="fictional time order"):
        resolve_spec({"test_scene_id": "second", "scenes": [first, second]}, tmp_path)
    second["at"] = "2026-09-10T18:10:00"
    with pytest.raises(ValueError, match="overlapping windows"):
        resolve_spec({"test_scene_id": "second", "scenes": [first, second]}, tmp_path)

def test_nonexistent_local_clock_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-03-08T02:30:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="nonexistent local time"):
        resolve_spec(spec, tmp_path)

def test_conflicting_explicit_offset_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-09-10T19:00:00-05:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="offset conflicts"):
        resolve_spec(spec, tmp_path)

def test_ambiguous_local_clock_rejected(tmp_path):
    spec = {"timezone": "America/New_York", "scenes": [{
        "at": "2026-11-01T01:30:00", "scene": "They make a plan.",
        "facts": ["The plan is set."]}]}
    with pytest.raises(ValueError, match="ambiguous local time"):
        resolve_spec(spec, tmp_path)
    spec["scenes"][0]["at"] = "2026-11-01T01:30:00-05:00"
    scenes, _, _ = resolve_spec(spec, tmp_path)
    assert scenes[0].at == "2026-11-01T01:30:00-05:00"


def test_cached_text_is_reverified_after_cross_chat_context_changes(tmp_path):
    from pacbench.prepare import reuse_realization,realization_binding
    from pacbench.authoring import SceneRecord,SceneJudgeError
    plan=ScenePlan(id='group',channel='imessage',thread='group',participants=('Me','Maya','Leo'),
        scene='Leo joins the already agreed outing.',facts=("They are going to Rosa's.",),at='2026-10-13T09:00:00-04:00')
    lines=(('Maya',"Join us at Luigi's?"),('Me','Yes.'),('Leo','Count me in.'))
    prior=(('2026-10-12T17:00:00-04:00','imessage:maya','Maya',"Rosa's Friday?"),)
    record=vars(SceneRecord(scene_id='group',status='ok',judge_calls=1))
    previous={'lines':lines,'record':record,'verdict':{'supported':True},
              'verification_binding':realization_binding(plan,lines,())}
    prompts=[]
    def reject(prompt):
        prompts.append(prompt)
        assert "[imessage:maya] Maya: Rosa's Friday?" in prompt
        return json.dumps({'supported':False,'evidence':[1], 'problem':'Wrong venue from an older week.',
            'facts':[{'index':1,'supported':False,'evidence':[1],'problem':'Wrong venue.'}]})
    with pytest.raises(SceneJudgeError,match='Wrong venue'):
        reuse_realization(plan,previous,reject,model='test',record_dir=tmp_path,verification_lines=prior)
    assert len(prompts)==1
    # Identical reviewed context uses the frozen text and verdict, with no calls.
    previous['verification_binding']=realization_binding(plan,lines,prior)
    reused=reuse_realization(plan,previous,lambda _:pytest.fail('Repeated unchanged verification'),
        model='test',record_dir=tmp_path,verification_lines=prior)
    assert reused.lines==lines


def test_resume_checkpoint_preserves_unvisited_scenes_and_their_original_specs(tmp_path):
    from pacbench.prepare import load_scene_cache
    from pacbench.followups import save
    old={'scenes':[{'id':'first','facts':['Old fact.']},{'id':'later','facts':['Later fact.']}]}
    rows=[{'scene_id':'first','lines':[['Me','First']]},{'scene_id':'later','lines':[['Me','Keep this render']]}]
    save(tmp_path/'resolved-spec.json',old);save(tmp_path/'scenes.json',{'scenes':rows})
    cache=load_scene_cache(tmp_path);save(tmp_path/'model-cache/scene-realizations.json',cache)
    save(tmp_path/'resolved-spec.json',{'scenes':[{'id':'first','facts':['Changed fact.']}]})
    save(tmp_path/'scenes.json',{'scenes':[rows[0]]})  # Interrupted after first recheck.
    recovered=load_scene_cache(tmp_path)
    assert recovered['later']['realization']==rows[1]
    assert recovered['first']['spec']['facts']==['Old fact.']


def test_frozen_sources_are_bound_to_the_actual_checked_transcript(tmp_path):
    from pacbench.preparation_audit import audit_scenes
    from pacbench.authoring import build_scene_observations,scene_spec
    from pacbench.followups import save,digest
    plan=ScenePlan(id='one',thread='chat',participants=('Me','Maya'),scene='Agree on pizza.',
        at='2026-10-12T18:00:00-04:00')
    lines=[['Me','Pizza Friday?'],['Maya','Yes.']];spec=scene_spec(plan)
    verdict={'supported':True};prompt='Synthetic independent checker input'
    review={'passed':True,'spec':spec,'dialogue':['Me: Pizza Friday?','Maya: Yes.'],
        'verdict':verdict,'judge_prompt':prompt,'judge_response':'supported','model_call':'original/path/call.json'}
    save(tmp_path/'scene-verifications/check.json',review)
    save(tmp_path/'model-calls/call.json',{'status':'ok','text':'supported',
        'request':{'messages':[{'role':'user','content':prompt}]}})
    messages=[o.public() for o in build_scene_observations('life',plan,lines)]
    row={'scene_id':'one','lines':lines,'source_ids':[m['source_id'] for m in messages],
        'record':{'status':'ok','judgment_paths':['original/path/check.json']},'verdict':verdict,
        'verification_binding':digest({'version':spec['judge_prompt_version'],'prompt':prompt})}
    save(tmp_path/'scenes.json',{'scenes':[row]})
    assert audit_scenes(tmp_path,{'scenes':[spec]},'life',messages)==[]
    messages[0]['text']='A different unchecked message'
    assert 'Frozen source bodies or metadata differ from verified scenes' in audit_scenes(tmp_path,{'scenes':[spec]},'life',messages)
    row['lines'][0][1]='An unchecked edit';save(tmp_path/'scenes.json',{'scenes':[row]})
    assert 'one: source differs from its passing checker input' in audit_scenes(tmp_path,{'scenes':[spec]},'life',messages)


def test_cast_is_private_scoped_and_bound_to_scene_cache(tmp_path):
    from pacbench.prepare import generation_spec, realization_binding
    from pacbench.authoring import scene_spec, build_scene_observations
    spec = json.loads(open('weeks/bike_pickup.json').read())
    plans, _, _ = resolve_spec(spec, tmp_path, week_start='2026-10-19')
    plan = plans[0]
    assert plan.cast == {'Riley': "The user's friend who works at Spoke Cycles."}
    assert 'friend who works at Spoke Cycles' in scene_prompt(plan)
    assert 'friend who works at Spoke Cycles' in judge_prompt(plan, [('Riley', 'Ready')])
    assert 'cast' not in build_scene_observations('life', plan, [('Riley', 'Ready')])[0].public().get('structured', {})
    changed = ScenePlan(**{**scene_spec_input(plan), 'cast': {'Riley': 'A mechanic.'}})
    assert generation_spec(plan) != generation_spec(changed)
    assert realization_binding(plan, [('Riley', 'Ready')], ()) != realization_binding(changed, [('Riley', 'Ready')], ())
    spec['cast'] = {'Unknown': 'A friend.'}
    with pytest.raises(ValueError, match='Cast'):
        resolve_spec(spec, tmp_path, week_start='2026-10-19')


def scene_spec_input(plan):
    from dataclasses import fields
    return {f.name: getattr(plan, f.name) for f in fields(plan)}


def test_scene_facts_share_one_checker_call():
    from pacbench.authoring import judge_scene, SceneJudgeError
    plan = ScenePlan(scene='A chat.', participants=('Me', 'Sam'), facts=('First', 'Second', 'Third'))
    calls = []
    def judge(prompt):
        calls.append(prompt)
        return json.dumps({'supported': True, 'evidence': [1], 'problem': '', 'facts': [
            {'index': i, 'supported': i != 2, 'evidence': [1], 'problem': ''} for i in range(1, 4)]})
    with pytest.raises(SceneJudgeError):
        judge_scene(plan, [('Sam', 'Hello')], judge)
    assert len(calls) == 1


def test_cast_is_scoped_to_channel_and_absent_cast_preserves_spec(tmp_path):
    from pacbench.authoring import scene_spec
    from pacbench.prepare import normalize_week
    spec = json.loads(open('weeks/bike_pickup.json').read())
    original = normalize_week(spec, '2026-10-19')
    spec['channels']['other'] = {'type': 'imessage', 'participants': ['Me', 'Sam']}
    spec['weeks'][0]['monday']['scenes'][1]['channel'] = 'other'
    rows = normalize_week(spec, '2026-10-19')['scenes']
    assert rows[0]['cast'] == spec['cast']
    assert 'cast' not in rows[1]
    del spec['cast']
    rows = normalize_week(spec, '2026-10-19')['scenes']
    assert all('cast' not in row for row in rows)
    plan = ScenePlan(scene='A chat.', participants=('Me', 'Sam'))
    assert 'cast' not in scene_spec(plan)
    assert 'Private character context' not in scene_prompt(plan)
    assert 'Private character context' not in judge_prompt(plan, [('Sam', 'Hi')])
    assert original['scenes'][0]['cast'] == {'Riley': "The user's friend who works at Spoke Cycles."}
