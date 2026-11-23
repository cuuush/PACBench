from copy import deepcopy

import pytest

from pacbench import honcho_week_state as week
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.honcho_first_answer_admission import check_prepared, verify_prior


def test_week_schedule_preserves_all_turns_and_only_the_eligible_prefix(tmp_path):
    docs,turns,schedule=week.plan()
    assert [len(doc.source_ids) for doc in docs]==[39,23,27,24,9,10,10]
    assert [q['id'] for q in schedule['turns']]==[t.id for t in turns]
    assert (len(turns),schedule['gradable_turns'],schedule['criteria'])==(49,47,192)
    assert len({q['authored_session'] for q in schedule['turns']})==31
    assert schedule['turns'][-1]['ready_files']==[doc.filename for doc in docs]
    root=tmp_path/'honcho'
    (root/'week-turns').mkdir(parents=True)
    scope=dict(root=str(root),plan=[{'at':doc.cutoff} for doc in docs])
    week.release_scope(scope,docs[0].cutoff)
    with pytest.raises(OwnershipConflict,match='after the next'):
        week.release_scope(scope,docs[1].cutoff)
    week.path_for(root,1,'verified').write_text('{}')
    with pytest.raises(OwnershipConflict,match='turn prefix'):
        week.pending(scope)


def test_honcho_continuation_binds_native_prior_history():
    user=dict(role='user',content='Frozen prompt',api_content='Frozen prompt plus old automatic recall',
              tool_calls=None,tool_name=None,tool_call_id=None)
    answer=dict(role='assistant',content='Native final answer',api_content=None,tool_calls=None,
                tool_name=None,tool_call_id=None)
    history=[user,answer]
    wire=[dict(role=row['role'],content=row['content']) for row in history]
    verify_prior(history,deepcopy(history),wire)
    with pytest.raises(OwnershipConflict):
        verify_prior(history,deepcopy(history),[wire[0],{**wire[1],'content':'Changed answer'}])
    with pytest.raises(OwnershipConflict):
        verify_prior(history,[user,{**answer,'content':'Replaced in the native DB'}],wire)


def test_new_question_may_open_once_after_a_settled_predecessor():
    earlier={'workflow_slot':{'question_id':'earlier','honcho_first_answer':{'tool_rounds':[]}}}
    current={'workflow_slot':{'question_id':'current','honcho_first_answer':{'tool_rounds':[]}}}
    check_prepared({'prior':earlier},current)
    with pytest.raises(OwnershipConflict):
        check_prepared({'prior':earlier,'replay':current},current)
