"""Import qualification must never execute or score benchmark questions."""

import pytest

def test_conversation_probe_preserves_one_unit_and_refuses_future_day_duplicates():
    import copy
    import pytest
    from pacbench.workflows.recording import select_conversation_probe
    rows=[{'source_id':str(i),'channel':'imessage','thread':'thread-one','collected_at':'2026-10-06T09:00:00-04:00',
           'written_at':f'2026-10-06T08:0{i}:00-04:00','sender':f'speaker-{i%2}','text':f'exact body {i}'} for i in range(4)]
    before=copy.deepcopy(rows)
    assert select_conversation_probe(rows,['0','1','2','3'],'America/New_York')==before and rows==before
    with pytest.raises(ValueError,match='distinct'):select_conversation_probe(rows,['0','0','2','3'],'America/New_York')
    with pytest.raises(ValueError,match='unknown'):select_conversation_probe(rows,['0','1','2','missing'],'America/New_York')
    rows[-1]['collected_at']='2026-10-07T09:00:00-04:00'
    with pytest.raises(ValueError,match='visibility'):select_conversation_probe(rows,['0','1','2','3'],'America/New_York')
    rows[-1]=before[-1];rows[-1]['thread']='other'
    with pytest.raises(ValueError,match='conversation'):select_conversation_probe(rows,['0','1','2','3'],'America/New_York')
