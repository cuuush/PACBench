import sqlite3
import pytest

from pacbench.context_isolation import memory_delivery


@pytest.mark.parametrize('prefetch,sidecar,state',[
    ('retained fact','question\n\n<memory-context>\nretained fact\n</memory-context>','confirmed'),
    ('retained fact','question\n\n<memory-context>\nstale fact\n</memory-context>','inconsistent'),
    ('retained fact','different question\n\n<memory-context>retained fact</memory-context>','unknown'),
    ('retained fact',None,'unknown'),
    ('',None,'unknown'),
    ('','question','empty'),
    ('','question\n\n<memory-context>different hook</memory-context>','inconsistent'),
    ('retained fact','question\n\n<memory-context>different hook</memory-context>retained fact','inconsistent'),
])
def test_saved_display_text_does_not_substitute_for_api_delivery(tmp_path,prefetch,sidecar,state):
    with sqlite3.connect(tmp_path/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,api_content TEXT)')
        db.execute('INSERT INTO messages VALUES (1,?,?,?,?)',('session','user','question',sidecar))
    record=memory_delivery(tmp_path,'session','question',prefetch)
    assert record['state']==state


def test_absent_native_evidence_is_unknown(tmp_path):
    assert memory_delivery(tmp_path,'session','question','retained fact')['state']=='unknown'
    assert not (tmp_path/'state.db').exists()

def test_repeated_prompt_binds_each_delivered_message(tmp_path):
    with sqlite3.connect(tmp_path/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,api_content TEXT)')
        for i, value in enumerate(('first','second'), 1):
            text = f'Again?\n\n<memory-context>{value}</memory-context>'
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?)',(i,'s','user','Again?',text))
    for i,value in enumerate(('first','second'),1):
        wire = f'Again?\n\n<memory-context>{value}</memory-context>'
        assert memory_delivery(tmp_path,'s','Again?',value,native_row_id=i,delivered_text=wire)['state']=='confirmed'
        assert memory_delivery(tmp_path,'s','Again?',value,native_row_id=3-i,delivered_text=wire)['state']=='unknown'
        assert memory_delivery(tmp_path,'s','Again?',value,native_row_id=i,delivered_text=None)['state']=='unknown'
