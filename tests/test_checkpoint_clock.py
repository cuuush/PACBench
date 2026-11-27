import hashlib,json,os,sqlite3,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from pacbench import provider_clock as clock
from pacbench.corpus.r2 import checkpoint_plan


@pytest.fixture
def staged(tmp_path,monkeypatch):
    home=tmp_path/'hermes-home';home.mkdir();at=datetime.now(timezone.utc).isoformat()
    c=clock.CheckpointClock(home,at,'baseline');env=c.environment({'HERMES_HOME':str(home),'PACBENCH_NOW':at})
    for k,v in env.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr('pacbench.hermes_gateway._identity',lambda *a:{})
    from pacbench.hermes_gateway import _start_time
    owner=SimpleNamespace(home=home,witness={'pid':os.getpid(),'start_time':_start_time(os.getpid())},fence=lambda:{'sessions':[],'native_failures':[]})
    (c.folder/'python-adoptions').mkdir()
    (c.folder/'python-adoptions'/f'{os.getpid()}.json').write_text(json.dumps({'pid':os.getpid(),'process_start_ticks':owner.witness['start_time'],'role':'hermes-gateway'}))
    d=c.prepare(owner,'exact question',at)
    with sqlite3.connect(home/'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT)');db.execute('INSERT INTO sessions VALUES ("fresh")')
        db.execute('CREATE TABLE messages(session_id TEXT, role TEXT, content TEXT)')
    return c,owner,d,env


def test_descriptor_is_immutable_clock_bound_and_exact_text(staged):
    c,owner,d,env=staged
    assert clock.descriptor()==d and clock.benchmark_env('PACBENCH_NOW')==d['at']
    with pytest.raises(RuntimeError,match='text differs'):clock.bind('fresh','r','altered question')
    clock.bind('fresh','request','exact question')
    with pytest.raises(FileExistsError):clock.bind('fresh','request','exact question')
    c.path.write_text('+0.000000\n')
    with pytest.raises(RuntimeError,match='changed while active'):clock.descriptor()


def test_empty_nonexistent_or_previously_answered_session_is_not_reusable(staged):
    c,owner,d,env=staged
    with pytest.raises(RuntimeError,match='does not exist'):clock.bind('missing','r','exact question')
    with sqlite3.connect(owner.home/'state.db') as db:db.execute('INSERT INTO messages VALUES ("fresh","user","prior")')
    with pytest.raises(RuntimeError,match='empty fresh'):clock.bind('fresh','r','exact question')


def test_pointer_tamper_and_unknown_clock_fail_before_request(staged):
    c,owner,d,env=staged
    pointer=c.folder/'active.json';value=json.loads(pointer.read_text());value['nonce']='../../outside';pointer.write_text(json.dumps(value))
    with pytest.raises(RuntimeError,match='nonce'):clock.descriptor()
    value['protocol']='unknown';pointer.write_text(json.dumps(value))
    with pytest.raises(RuntimeError,match='Unknown'):clock.descriptor()


def test_next_question_needs_full_fence_and_no_rewind(staged):
    c,owner,d,env=staged
    def unclean():raise RuntimeError('pending native writer')
    owner.fence=unclean
    with pytest.raises(RuntimeError,match='pending native writer'):c.prepare(owner,'next',d['at'])
    owner.fence=lambda:{}
    with pytest.raises(RuntimeError,match='cannot rewind'):c.prepare(owner,'next',(datetime.fromisoformat(d['at'])-timedelta(minutes=1)).isoformat())


def test_provider_cannot_share_the_owned_observation_clock(staged):
    c,owner,d,env=staged
    from pacbench.hermes_gateway import _start_time
    pid=os.getppid();(c.folder/'python-adoptions'/f'{pid}.json').write_text(json.dumps({'pid':pid,'process_start_ticks':_start_time(pid),'role':'native-provider'}))
    with pytest.raises(RuntimeError,match='another live consumer'):
        c.prepare(owner,'next',(datetime.fromisoformat(d['at'])+timedelta(minutes=10)).isoformat())


def test_direct_input_receipt_separates_process_and_question_identities(staged,monkeypatch):
    c,owner,d,env=staged
    path=owner.home.parent/'api-inputs.jsonl';monkeypatch.setenv('PACBENCH_API_INPUT_TRACE',str(path));monkeypatch.setenv('PACBENCH_PYTHON_CLOCK_ROLE','hermes-gateway')
    from pacbench.context_isolation import record_input as record
    record('test-driver',{'id':'exact-attempt','request':{'model':'openai/gpt-6-luna','messages':[{'role':'user','content':'exact question'}]}})
    row=json.loads(path.read_text());assert row['protocol']=='direct-hermes-api-input-v3'
    assert row['process_clock_nonce']==c.process_nonce and row['turn_nonce']==d['nonce'] and row['question_sha256']==d['question_sha256']


@pytest.mark.parametrize('provider',['memcal','mem0','reme'])
def test_checkpoint_plan_preserves_distinct_times_text_and_chronological_visibility(provider):
    questions=[{'id':'a','at':'2026-10-06T09:00:00-04:00','prompt':'first','facts':['a']},
        {'id':'b','at':'2026-10-06T09:10:00-04:00','prompt':'second','facts':['b']},
        {'id':'c','at':'2026-10-07T09:00:00-04:00','prompt':'third','facts':['c']}]
    batches=[{'id':'monday','at':'2026-10-05T23:59:00-04:00'},{'id':'tuesday','at':'2026-10-06T23:59:00-04:00'}]
    p=checkpoint_plan({'checks':questions,'timezone':'America/New_York'},batches,provider)
    assert len(p['groups'])==2 and p['groups'][0]['ready_batch_ids']==['monday']
    assert p['groups'][1]['ready_batch_ids']==['monday','tuesday']
    assert [q['at'] for g in p['groups'] for q in g['questions']]==[q['at'] for q in questions]
    assert p['groups'][0]['questions'][0]['prompt_sha256']==hashlib.sha256(b'first').hexdigest()
    assert p['live_qualified'] is False and not p['times_collapsed']


def test_plan_uses_frozen_evaluated_question_and_criteria_not_authored_fact_count():
    question={'id':'one','at':'2026-10-06T09:00:00-04:00','prompt':'Authored wording','facts':['one author fact']}
    contract={'question_at':question['at'],'question':'Reviewed exact wording','claims':[{'id':'a'},{'id':'b'}]}
    contract['contract_hash']=hashlib.sha256(json.dumps(contract,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    p=checkpoint_plan({'checks':[question],'timezone':'America/New_York'},[],'mem0',contracts={'one':contract})
    assert p['criteria']==2 and p['authored_facts']==1 and p['groups'][0]['questions'][0]['prompt']=='Reviewed exact wording'
    contract['question']='changed'
    with pytest.raises(ValueError,match='contract hash'):checkpoint_plan({'checks':[question],'timezone':'America/New_York'},[],'mem0',contracts={'one':contract})


def test_continuation_binds_full_committed_native_history_including_api_content(staged):
    c,owner,d,_=staged
    with sqlite3.connect(owner.home/'state.db') as db:
        db.execute('DROP TABLE messages')
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_name TEXT,api_content TEXT)')
        db.execute("INSERT INTO messages VALUES(1,'fresh','user','first',NULL,'first plus typed memory')")
        db.execute("INSERT INTO messages VALUES(2,'fresh','assistant','answer',NULL,NULL)")
    h=clock.history(owner.home,'fresh')
    c.prepare(owner,'follow',d['at'],continuation={'initial_at':d['at'],'continued_session_id':'fresh','history_sha256':h['sha256']})
    # Public sender text is unchanged: a hidden API-bound mutation still blocks.
    with sqlite3.connect(owner.home/'state.db') as db:db.execute("UPDATE messages SET api_content='unexpected source text' WHERE id=1")
    with pytest.raises(RuntimeError,match='history differs'):clock.bind('fresh','next','follow')
