import sqlite3
import pytest

from pacbench.context_isolation import memory_delivery


@pytest.mark.parametrize('prefetch,sidecar,state',[
    ('retained fact','question\n\n<memory-context>\nretained fact\n</memory-context>','confirmed'),
    ('retained fact','question\n\n<memory-context>\nstale fact\n</memory-context>','inconsistent'),
    ('retained fact','different question\n\n<memory-context>retained fact</memory-context>','inconsistent'),
    ('retained fact',None,'inconsistent'),
    ('',None,'empty'),
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
