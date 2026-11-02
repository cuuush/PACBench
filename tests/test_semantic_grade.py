import json

import pytest

from tests.test_evaluation import _combined_fixture


from pacbench.authoring import (ScenePlan, SceneRenderError,
                                build_scene_observations, parse_scene_lines,
                                scene_prompt, judge_scene, SceneJudgeError,
                                realize_scene)
from pacbench.semantic_grade import Claim, grade_semantic
from pacbench.semantic_contract import build_contract, load_contract


def test_contract_source_view_preserves_chat_context_for_unnamed_cancellations():
    from pacbench.semantic_contract import _dialogue
    shared={'written_at':'2026-10-06T07:00:00-04:00','sender':'Jen','text':"I can't make it today"}
    rendered=_dialogue([
        {**shared,'source_id':'group-1','channel':'imessage','thread':'pond-crew'},
        {**shared,'source_id':'dm-1','channel':'imessage','thread':'jen'}])
    assert 'group-1 | written' in rendered and 'channel imessage | thread pond-crew | Jen:' in rendered
    assert 'dm-1 | written' in rendered and 'channel imessage | thread jen | Jen:' in rendered


def test_exported_message_and_thread_ids_do_not_reveal_authoring_outcomes():
    first=ScenePlan(id='party-cancelled',thread='pond-cancelled',channel='imessage',
        scene='The group postpones an outing.',participants=('Me','Jen'),at='2026-10-06T07:00:00-04:00')
    second=ScenePlan(id='party-reinstated',thread='pond-cancelled',channel='imessage',
        scene='The same group follows up.',participants=('Me','Jen'),at='2026-10-07T07:00:00-04:00')
    lines=[('Jen',"I can't make it today"),('Me','No worries')]
    a=build_scene_observations('author-week',first,lines)
    b=build_scene_observations('author-week',second,lines)
    assert a==build_scene_observations('author-week',first,lines)
    assert a[0].thread==b[0].thread
    assert {o.source_id for o in a}.isdisjoint(o.source_id for o in b)
    assert all(word not in json.dumps([o.public() for o in a+b]) for word in ('author-week','party-cancelled','party-reinstated','pond-cancelled'))


@pytest.mark.parametrize('citation',['s1','s999'])
def test_draft_citation_aliases_resolve_exactly_and_unknown_labels_fail(tmp_path,citation):
    source='message-97d543a2ea1f3d30a9a18b2c'
    observations=[{'source_id':source,'sender':'Me','written_at':'2026-10-05T18:00:00-04:00',
        'channel':'imessage','thread':'thread-opaque','text':'Dinner Friday at seven.'}]
    def draft(prompt):
        assert 's1 | written' in prompt and source not in prompt
        return json.dumps({'claims':[{'id':'time','statement':'Dinner is at seven.','evidence':[citation]}]})
    def review(prompt):
        assert source in prompt
        if 'Check this evaluation prompt' in prompt:
            return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'The question asks for the time.'})
        return json.dumps({'complete':True,'reviews':[{'id':'time','supported':True,'relevant':True,'unambiguous':True,'reason':'The source states the time.'}]})
    question=lambda prompt:json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'The question asks for the time.'})
    kwargs=dict(scene='Dinner',test_intent='Dinner is at seven.',observations=observations,
        generate=draft,verify=_combined_fixture(review),question_generate=question,user_question='What time is dinner?',
        question_at='2026-10-11T21:00:00-04:00',output_path=tmp_path/'contract.json')
    if citation=='s999':
        with pytest.raises(ValueError,match='unsupported citation'):build_contract(**kwargs)
    else:
        result=build_contract(**kwargs)
        assert result.claims[0].evidence==(source,)
        assert load_contract(tmp_path/'contract.json')==result


def test_description_alone_generates_and_judges_a_scene():
    scene = ScenePlan(scene="Devon and the user making plans to get pizza Friday night",
                      test_intent="Recall the agreed plan",
                      window_start="2026-09-10T18:00:00",
                      window_end="2026-09-10T18:20:00")
    def render(prompt):
        assert "Devon and the user" in prompt
        return "Devon: Pizza Friday night?\nMe: Yes, let's do it!"
    def judge(prompt):
        assert "Pizza Friday night?" in prompt
        return json.dumps({"supported": True, "evidence": [1, 2], "problem": ""})
    result = realize_scene(scene, render, judge)
    obs = build_scene_observations("pizza", scene, list(result.lines))
    assert [o.sender for o in obs] == ["Devon", "Me"]
    assert [o.from_owner for o in obs] == [False, True]
    assert result.record.status == "ok"


@pytest.mark.parametrize("speaker", ["me", "User", "Owner", "I"])
def test_ambiguous_owner_label_fails_scene_build(speaker):
    plan = ScenePlan(scene="A conversation", test_intent="Recall it")
    with pytest.raises(SceneRenderError):
        parse_scene_lines(f"Devon: Hi\n{speaker}: Hello", plan)


def test_scene_retry_continues_writers_draft_after_verifier_rejection():
    plan = ScenePlan(scene="Agree on Friday pizza with Devon",
                     test_intent="Recall the plan")
    class Writer:
        def __call__(self, prompt):
            return "Devon: Pizza Friday?\nMe: Maybe."
        def complete(self, messages):
            assert [m["role"] for m in messages] == ["user", "assistant", "user"]
            assert messages[1]["content"] == "Devon: Pizza Friday?\nMe: Maybe."
            assert "not agreed" in messages[2]["content"]
            return "Devon: Pizza Friday?\nMe: Yes, let's do it."
    calls = 0
    def verifier(_):
        nonlocal calls
        calls += 1
        return json.dumps({"supported": calls == 2, "evidence": [1, 2],
                           "problem": "" if calls == 2 else "not agreed"})
    result = realize_scene(plan, Writer(), verifier)
    assert result.record.attempts == 2
    assert result.lines[-1] == ("Me", "Yes, let's do it.")


def test_rendered_json_keeps_writer_note_out_of_frozen_messages():
    plan = ScenePlan(scene="A casual chat leading to a plan.")
    result = realize_scene(
        plan,
        lambda _: json.dumps({"dialogue": "Sam: Ice cream?\nMe: Yes!",
                              "writer_note": "The brief greeting makes the invitation feel casual."}),
        lambda _: json.dumps({"supported": True, "evidence": [1, 2], "problem": ""}))
    assert result.lines == (("Sam", "Ice cream?"), ("Me", "Yes!"))
    assert result.record.writer_notes == ["The brief greeting makes the invitation feel casual."]
    assert "writer_note" not in [o.text for o in build_scene_observations("chat", plan, list(result.lines))]


def test_test_intent_is_author_prose_and_private_to_scene_writer():
    plan = ScenePlan(scene="Sam and I arrange a movie.",
                     test_intent="Recall the movie and our purchased seats.")
    assert plan.scene in scene_prompt(plan)
    assert plan.test_intent not in scene_prompt(plan)


def test_pizza_day_facts_keep_date_and_time_separate_without_requiring_year():
    from pathlib import Path
    spec = json.loads((Path(__file__).parents[1] / "weeks/pizza_day.json").read_text())
    plan = ScenePlan(**spec["scenes"][0])
    assert len(plan.facts) == 5
    assert plan.facts[3] == "The pizza plan is for Friday, September 11."
    assert plan.facts[4] == "The agreed time is 7pm."


def test_fact_authored_thread_uses_prose_facts_and_verifies_each_one():
    plan = ScenePlan(scene="Mike and Sarah chat, then arrange ice cream.",
                     facts=("The meetup is Wednesday.", "The meetup is at 5pm."),
                     window_start="2026-10-01T17:00:00")
    prompt = scene_prompt(plan)
    assert "Wednesday" in prompt and "5pm" in prompt
    assert "Choose the number of messages" in prompt
    lines = [("Mike", "Ice cream Wednesday?"),
             ("Sarah", "Five works for me."), ("Me", "Great, see you Wednesday at five!")]
    def verified(_):
        return json.dumps({"supported": True, "evidence": [1, 2, 3],
                           "problem": "", "facts": [
                               {"index": 1, "supported": True, "evidence": [1, 3], "problem": ""},
                               {"index": 2, "supported": True, "evidence": [2, 3], "problem": ""}]})
    verdict, *_ = judge_scene(plan, lines, verified)
    assert len(verdict["facts"]) == 2
    with pytest.raises(SceneJudgeError):
        judge_scene(plan, lines, lambda _: json.dumps({
            "supported": True, "evidence": [1], "problem": "", "facts": []}))


def test_fact_authored_contract_requires_one_evidence_backed_claim_each(tmp_path):
    facts = ("The meeting is Wednesday.", "The meeting is at 5pm.")
    observations = [{"source_id": "chat/0", "sender": "Mike",
                     "written_at": "2026-10-01T17:00:00",
                     "text": "Wednesday at five works."}]
    rows = {"claims": [
        {"id": "fact-1", "statement": "The meeting is Wednesday.", "evidence": ["chat/0"]},
        {"id": "fact-2", "statement": "Five o'clock is the meeting time.", "evidence": ["chat/0"]}]}
    def verify(prompt):
        if "Check the proposed user question" in prompt:
            return json.dumps({"safe": True, "faithful": True, "reason": "neutral"})
        return json.dumps({"complete": True, "coverage_reason": "both facts",
                           "reviews": [{"id": f"fact-{i}", "supported": True,
                                        "relevant": True, "unambiguous": True,
                                        "reason": "in dialogue"} for i in (1, 2)]})
    path = tmp_path / "contract.json"
    contract = build_contract(scene="They arrange a meeting", test_intent="",
        author_facts=facts, observations=observations,
        generate=lambda _: json.dumps(rows), verify=verify,
        question_generate=lambda _: '{"question":"When is the meeting?"}',
        output_path=path)
    assert len(contract.claims) == 2
    assert [claim.statement for claim in contract.claims] == list(facts)
    assert json.loads(path.read_text())["author_fact_count"] == 2
    with pytest.raises(ValueError, match="exactly one claim"):
        build_contract(scene="They arrange a meeting", test_intent="",
            author_facts=facts, observations=observations,
            generate=lambda _: json.dumps({"claims": rows["claims"][:1]}),
            verify=verify, question_generate=lambda _: "unused",
            output_path=tmp_path / "bad.json")


def test_semantic_grader_checks_three_independent_facts_without_matching():
    claims = (Claim("who", "Pizza is with Devon"),
              Claim("where", "Pizza is at Luigi's"),
              Claim("when", "Pizza is Friday at 7pm"))
    def judge(prompt):
        assert "who" in prompt and "where" in prompt and "when" in prompt
        return json.dumps({"verdicts": [
            {"id": "who", "verdict": "correct", "reason": "names Devon"},
            {"id": "where", "verdict": "missed", "reason": "no venue"},
            {"id": "when", "verdict": "misrepresented", "reason": "says Saturday"}]})
    grade = grade_semantic("pizza/plan", "When and where?", "Devon says Saturday", claims, judge)
    assert (grade.correct, grade.missed, grade.misrepresented) == (1, 1, 1)
    assert grade.all_claims_correct is False
    assert grade.claim_coverage == pytest.approx(1 / 3)
    assert not hasattr(grade, "precision")


def test_semantic_grader_rejects_partial_or_invalid_verdict():
    claims = (Claim("who", "with Devon"), Claim("where", "at Luigi's"))
    with pytest.raises(ValueError, match="unusable"):
        grade_semantic("pizza/plan", "Where?", "I don't know", claims,
                       lambda _: '{"verdicts":[{"id":"who","verdict":"correct","reason":"yes"}]}')


def test_contract_uses_frozen_dialogue_timestamp_and_rejects_bad_review(tmp_path):
    observations = [{"source_id": "x/0", "sender": "Me",
                     "written_at": "2026-09-10T18:00:00",
                     "text": "Let's meet at 7pm on Friday."}]
    proposed = json.dumps({"claims": [{"id": "date",
        "statement": "The meeting is Friday, September 11, 2026.",
        "evidence": ["x/0"]}]})
    reviewed = json.dumps({"complete": True, "coverage_reason": "date covered",
        "reviews": [{"id": "date", "supported": True, "relevant": True,
                     "unambiguous": True, "reason": "timestamp resolves Friday"}]})
    calls = []
    def generate(prompt):
        calls.append(prompt)
        return proposed
    def verify(prompt):
        return json.dumps({"safe": True, "faithful": True,
                           "reason": "generic"}) if "Check the proposed user question" in prompt else reviewed
    path = tmp_path / "contract.json"
    contract = build_contract(scene="A Friday meeting", test_intent="Recall the calendar date",
        observations=observations, generate=generate, verify=verify,
        question_generate=lambda _: '{"question":"When is the meeting?"}',
        output_path=path)
    assert "written 2026-09-10T18:00:00" in calls[0]
    assert load_contract(path) == contract
    with pytest.raises(FileExistsError):
        build_contract(scene="A Friday meeting", test_intent="Recall the calendar date",
            observations=observations, generate=generate, verify=verify,
            question_generate=lambda _: '{"question":"When is the meeting?"}',
            output_path=path)

    bad = json.dumps({"complete": True, "reviews": [{"id": "date",
        "supported": False, "relevant": True, "unambiguous": True,
        "reason": "not in dialogue"}]})
    with pytest.raises(ValueError, match="review rejected"):
        build_contract(scene="A Friday meeting", test_intent="Recall date",
            observations=observations, generate=lambda _: proposed,
            verify=lambda _: bad, question_generate=lambda _: "unused",
            output_path=tmp_path / "rejected.json")

    def reject_question(prompt):
        if "Check the proposed user question" in prompt:
            assert "Frozen dialogue:" in prompt
            return json.dumps({"safe": True, "faithful": False,
                               "reason": "question assigns the purchase to the wrong person"})
        return reviewed
    with pytest.raises(ValueError, match="unfaithful"):
        build_contract(scene="A Friday meeting", test_intent="Recall date",
            observations=observations, generate=lambda _: proposed,
            verify=reject_question,
            question_generate=lambda _: '{"question":"When did I buy the tickets?"}',
            output_path=tmp_path / "unfaithful.json")


def test_evidence_diagnostic_is_separate_and_requires_exact_provenance(tmp_path):
    from pacbench.semantic_grade import validate_diagnostic, DIAGNOSTIC_VERSION
    claims = (Claim('who', 'Devon attends', ('m1',)),)
    inputs = {'answer_visible': [{'id': 'tool-result-1', 'text': 'Devon attends', 'truncated': False}],
              'source_evidence': [{'id': 'm1', 'text': 'Devon attends. Lee declined.', 'truncated': False}],
              'source_scope': 'partial'}
    diagnostic = {'claims': [{'id': 'who', 'status': 'supported', 'reason': 'Shown',
                              'citations': [{'id': 'tool-result-1', 'quote': 'Devon attends'}]}],
                  'extra_assertions': [{'assertion': 'Lee attends', 'answer_quote': 'Lee attends',
                      'status': 'contradicted', 'reason': 'Lee declined',
                      'citations': [{'id': 'm1', 'quote': 'Lee declined'}]}]}
    response = {'verdicts': [{'id': 'who', 'verdict': 'correct', 'reason': 'Named Devon'}],
                'evidence_diagnostic': diagnostic}
    path = tmp_path / 'judgment.json'
    grade = grade_semantic('x', 'Who?', 'Devon and Lee attends', claims,
                           lambda _: json.dumps(response), record_path=path, diagnostic_inputs=inputs, diagnostic_version=DIAGNOSTIC_VERSION)
    assert grade.all_claims_correct and grade.evidence_diagnostic['state'] == 'complete'
    assert json.loads(path.read_text())['diagnostic_protocol'] == DIAGNOSTIC_VERSION
    diagnostic['extra_assertions'][0]['citations'][0]['quote'] = 'invented'
    assert validate_diagnostic(diagnostic, claims, 'Devon and Lee attends', inputs)['state'] == 'ungraded'
    response.pop('evidence_diagnostic')
    grade = grade_semantic('x', 'Who?', 'Devon', claims, lambda _: json.dumps(response), diagnostic_inputs=inputs)
    assert grade.all_claims_correct and grade.evidence_diagnostic['state'] == 'ungraded'


def test_versioned_evidence_prompt_and_unicode_quotes_are_exact():
    from pacbench.semantic_grade import (validate_diagnostic, judge_prompt,
        DIAGNOSTIC_VERSION, LEGACY_DIAGNOSTIC_VERSION)
    claims = (Claim('when', 'Friday'),)
    inputs = {'answer_visible': [{'id': 'tool-result-1', 'text': '〔E1〕 Friday at 7pm', 'truncated': False}],
              'source_evidence': []}
    good = {'claims': [{'id': 'when', 'status': 'supported', 'reason': 'Shown',
        'citations': [{'id': 'tool-result-1', 'quote': 'Friday at 7pm'}]}], 'extra_assertions': []}
    assert validate_diagnostic(good, claims, 'Friday', inputs, DIAGNOSTIC_VERSION)['state'] == 'complete'
    good['claims'][0]['citations'][0]['quote'] = '〈E1〉 Friday at 7pm'
    assert validate_diagnostic(good, claims, 'Friday', inputs, DIAGNOSTIC_VERSION)['state'] == 'ungraded'
    old = judge_prompt('When?', 'Friday', claims, diagnostic_inputs=inputs, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION)
    new = judge_prompt('When?', 'Friday', claims, diagnostic_inputs=inputs, diagnostic_version=DIAGNOSTIC_VERSION)
    assert 'Omit citation markers' not in old and 'Omit citation markers' in new
