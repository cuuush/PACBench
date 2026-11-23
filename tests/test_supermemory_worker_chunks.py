"""Verify the chunk read boundary before any paid Supermemory continuation."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from pacbench import supermemory_week_admission as week, supermemory_worker_chunks as chunks
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.supermemory_source_admission import BINARY_SHA
from pacbench.supermemory_source_effect import SourceEffect
from pacbench.workflow_progress import WorkflowProgress
from .test_supermemory_worker_rounds import BINARY, REPO, stock_fragments

DOC, UPDATE = 'd' * 22, 'u' * 22


def anchor(readback, revision, publication, at_ms=1):
    return dict(native_id=DOC, namespace='week', session_id='hermes-warm', revision=revision,
                readback=dict(chunk_anchor=dict(protocol=chunks.PROTOCOL, at_ms=at_ms, revision=revision,
                                                publication=publication, chunks=readback)))


def test_anchor_checks_one_stable_published_chunk_inventory():
    stamp = '2026-10-05T21:00:00-04:00'
    v3 = dict(id=DOC, customId='chat', containerTags=['week'], latestRevision=5, activeContentUpdateId='p' * 22,
              status='done', createdAt=stamp, updatedAt=stamp, content='message', metadata={})
    v5 = dict(content='message', metadata={}, system=dict(status='done', createdAt=stamp, updatedAt=stamp),
              chunks=[dict(id='c' * 22, position=0, content='message')])
    document = SimpleNamespace(filename='chat')
    proof = chunks.capture_anchor(v3, v5, v3, document, native_id=DOC, namespace='week')
    assert proof['revision'] == 5 and proof['chunks'][0]['sha256'] == sha256(b'message').hexdigest()
    for changed in (dict(v3, activeContentUpdateId='s' * 22), dict(v3, latestRevision=6)):
        with pytest.raises(OwnershipConflict, match='publication changed'):
            chunks.capture_anchor(v3, v5, changed, document, native_id=DOC, namespace='week')


def pinned_chunk_code(binary):
    """Place the actual observer callsites at their pinned source lines."""
    data = binary.read_bytes()
    assert sha256(data).hexdigest() == BINARY_SHA
    start = data.index(b'function hv5(')
    end = data.index(b'async function Mv5(', start)
    reference = data.index(b'.map((p)=>p.documentId)', end)
    projection = b'($)=>({id:$.id,position:$.position,content:$.content})'
    assert data.count(projection) == 1 and projection in data[data.index(b'async function Fv5(', start):end]
    prefix = data[data.rfind(b'\n', 0, start) + 1:start]
    line = 2999 - data[start:reference].count(b'\n')
    col = len(prefix.decode().encode('utf-16-le')) // 2
    tag = data.index(b'function iH0(')
    tag_end = data.index(b'function Zn8(', tag)
    transform = data.index(rb'.replace(/\\u(?:', tag, tag_end)
    tag_line = 1534 - data[tag:transform].count(b'\n')
    tag_column = len(data[data.rfind(b'\n', 0, tag) + 1:tag].decode().encode('utf-16-le')) // 2
    call = data.index(b'S=hv5(') + 2
    call_column = len(data[data.rfind(b'\n', 0, call) + 1:call].decode().encode('utf-16-le')) // 2

    def place(source, content, lineno, column):
        lines = lineno - source.count('\n') - 1
        used = 0 if lines else len(source.rsplit('\n', 1)[-1])
        assert lines >= 0 and column >= used
        return source + '\n' * lines + ' ' * (column - used) + content

    source = place("'use strict';\n(function(){", data[tag:tag_end].decode(), tag_line, tag_column)
    header = '\nconst kI = iH0({});\nconst render = z => {'
    assignment = 'const result = '
    source = place(source + header, assignment + 'hv5(z); return result; };', 2956, call_column - len(assignment))
    return '\n'.join([
        place(source, data[start:end].decode(), line, col),
        stock_fragments(binary),
        'return {Uv5,Fv5,hv5,render,ei8,rb8};})()',
    ])


def native_fixture(tmp_path, pg_bin):
    if not BINARY.is_file():
        pytest.skip('pinned Supermemory binary is unavailable')
    code = tmp_path / 'stock.js'
    code.write_text(pinned_chunk_code(BINARY))
    observer = REPO / 'pacbench/direct_runtime/supermemory_source_effect.cjs'
    fixture = tmp_path / 'probe.cjs'
    fixture.write_text(r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const {AsyncLocalStorage} = require('node:async_hooks');
const {execFileSync} = require('node:child_process');
const doc = 'd'.repeat(22), update = 'u'.repeat(22), c1 = 'c'.repeat(22), c2 = 'e'.repeat(22), c3 = 'f'.repeat(22);
const socket = process.env.OFFLINE_PG, db = process.env.OFFLINE_PSQL;
const sql = statement => execFileSync(db, ['-XAt', '-h', socket, '-d', 'postgres',
  '-v', 'ON_ERROR_STOP=1', '-c', statement], {encoding:'utf8'});
const versions = [
  [{id:c1,position:0,content:'first authored body'},{id:c2,position:1,content:'previous closing'}],
  [{id:c1,position:0,content:'first authored body'},{id:c3,position:1,content:'new closing'}],
];
const publish = revision => {
  activeRows = versions[revision];
  if (socket) sql(`BEGIN; UPDATE chunk SET state='retired';
    INSERT INTO chunk VALUES ${activeRows.map(r=>`('${r.id}','${doc}',${r.position},'${r.content}','active')`).join(',')};
    UPDATE document SET revision=${revision + 4}; COMMIT;`);
};
let activeRows;
if (socket) sql('CREATE TABLE IF NOT EXISTS document(revision int); CREATE TABLE IF NOT EXISTS chunk(id text, document_id text, position int, content text, state text); TRUNCATE document,chunk; INSERT INTO document VALUES (4);');
globalThis.fetch = () => { throw Error('offline fixture prohibits fetch'); };
globalThis.U0 = {id:'id',position:'position',content:'content',documentId:'document_id',state:'state'};
globalThis.G = (k,v) => [k,v]; globalThis.n0 = (k,v) => [k,v];
globalThis.m0 = (...x) => x; globalThis._1 = x => x;
globalThis.L0 = u => u.try();
globalThis.q0 = () => ({select: cols => ({from: () => ({where: conditions => ({orderBy: order => ({
  execute: async () => {
    assert.deepEqual(Object.keys(cols), ['id','position','content']);
    const where = Object.fromEntries(conditions);
    assert.equal(where.document_id, doc); assert.equal(where.state, 'active'); assert.equal(order, 'position');
    if (socket) {
      const positions = where.position ? ` AND position IN (${where.position.join(',')})` : '';
      return JSON.parse(sql(`SELECT coalesce(json_agg(a), '[]') FROM (
        SELECT id, position, content FROM chunk WHERE document_id='${doc}' AND state='active'${positions}
        ORDER BY position) a;`));
    }
    return activeRows.filter(r => !where.position || where.position.includes(r.position));
  },
})})})})});
const stock = vm.runInThisContext(fs.readFileSync(process.env.PROBE_STOCK,'utf8'),
  {filename:'/$bunfs/root/supermemory-server'});
const capture = {exports:{}};
const seed = vm.runInThisContext(`(function(require,module){${fs.readFileSync(process.env.PROBE_HOOK,'utf8')}
  return (storage, token, context) => contexts.set(storage, new WeakMap([[token, context]]));
})`, {filename:process.env.PROBE_HOOK})(require, capture);
const storage = new AsyncLocalStorage(), token = {};
const context = {members:[{update, document:doc}], next:0, active:null, initialRead:null, ambiguous:false};
seed(storage, token, context);
publish(0);
storage.run(token, async () => {
  const before = process.env.UPDATE_FIRST === '1';
  const ambiguous = process.env.AMBIGUOUS_READS === '1';
  if (before) publish(1);
  const opened = await stock.Uv5({},doc);
  // Publication after the opening SQL returns cannot change those returned bytes.
  if (!before && !ambiguous) publish(1);
  console.log(`[Workflow] Document ${doc} starting memory agent (${opened.totalChunks} chunks)`);
  const promptInput = {title:'Conversation',initialChunks:opened.chunks,totalChunks:opened.totalChunks,profileBuckets:[]};
  if (process.env.WRONG_PROMPT_CALLSITE === '1') {
    stock.hv5(promptInput);
    assert.equal(context.active.chunkTrace.preview,true);
    assert.equal(capture.exports.chunkWitness(),null);
    process.exit(0);
  }
  const prompt = stock.render(promptInput);
  assert.ok(capture.exports.chunkWitness(), JSON.stringify(context.active));
  const original = [{role:'system',content:'Stock worker.'},{role:'user',content:[{type:'text',text:prompt}]}];
  const toolName = 'readChunks', toolCallId = 'call-a', input = {positions:[ambiguous ? 0 : 1]};
  const output = {chunks: await stock.Fv5({},doc,input)};
  const parts = [{type:'tool-call',toolName,toolCallId,input},{type:'tool-result',toolName,toolCallId,input,output}];
  if (ambiguous) {
    publish(1);
    const second = {chunks: await stock.Fv5({},doc,input)};
    assert.deepEqual(output,second);
    parts.push({type:'tool-call',toolName,toolCallId:'call-b',input},
      {type:'tool-result',toolName,toolCallId:'call-b',input,output:second});
  }
  const step = await stock.ei8({content:parts,tools:{readChunks:{execute:() => output}}});
  const result = {body:{messages:stock.rb8([...original,...step])}, witness:capture.exports.chunkWitness(),
    round:capture.exports.roundWitness()};
  process.stdout.write(JSON.stringify(result)+'\n');
  process.exit(0);
}).catch(error => {console.error(error);process.exit(1);});
""")
    (tmp_path / 'bunfig.toml').write_text('preload = [' + json.dumps(str(fixture)) + ']\n')
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LANGUAGE', 'LC_ALL', 'LD_LIBRARY_PATH')}
    return {**env, 'HOME': str(tmp_path), 'PROBE_HOOK': str(observer), 'PROBE_STOCK': str(code),
            'PACBENCH_SUPERMEMORY_STOCK_ROUNDS': '1', 'PACBENCH_SUPERMEMORY_STOCK_CHUNKS': '1', **pg_bin}


@pytest.fixture
def pg_socket(tmp_path):
    """Use the installed PostgreSQL in scratch when supplied, with a private Unix socket."""
    folder = os.environ.get('PACBENCH_TEST_SUPERMEMORY_PG_BIN')
    if not folder:
        yield {}
        return
    bin_dir = Path(folder)
    socket, data = tmp_path / 'socket', tmp_path / 'db'
    socket.mkdir(mode=0o700)
    subprocess.run([bin_dir / 'initdb', '-D', data, '--no-locale', '--encoding=UTF8'], capture_output=True, check=True, timeout=30)
    ctl = [bin_dir / 'pg_ctl', '-D', data]
    subprocess.run([*ctl, '-l', tmp_path / 'pg.log', '-o', f"-c listen_addresses='' -k {socket}", '-w', 'start'],
                   capture_output=True, check=True, timeout=30)
    try:
        yield {'OFFLINE_PG': str(socket), 'OFFLINE_PSQL': str(bin_dir / 'psql')}
    finally:
        subprocess.run([*ctl, '-m', 'fast', '-w', 'stop'], capture_output=True, check=True, timeout=30)


def test_pinned_worker_observes_both_publication_orders(tmp_path, monkeypatch, pg_socket):
    env = native_fixture(tmp_path, pg_socket or {})
    drifted = subprocess.run([BINARY], cwd=tmp_path, env=dict(env, WRONG_PROMPT_CALLSITE='1'),
                             capture_output=True, text=True, timeout=15)
    assert drifted.returncode == 0, drifted.stderr
    payloads = []
    for before in (False, True):
        result = subprocess.run([BINARY], cwd=tmp_path, env=dict(env, UPDATE_FIRST=str(int(before))),
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        payloads.append(json.loads(result.stdout.splitlines()[-1]))

    first, second = payloads
    r4 = first['witness']['opening']['chunks']
    r5 = second['witness']['opening']['chunks']
    assert r4 != r5
    assert first['witness']['tools']['call-a']['chunks'] == second['witness']['tools']['call-a']['chunks'] == r5[1:]
    assert 'previous closing' in first['body']['messages'][1]['content']
    assert 'new closing' in second['body']['messages'][1]['content']
    assert 'new closing' in first['body']['messages'][-1]['content']

    _, turns, _, _ = week.plan()
    t4, t5 = turns[0], next(t for t in turns[1:] if t.session == turns[0].session)
    scope = dict(root=str(tmp_path), namespace='week')
    history = tmp_path / 'capture-membership' / DOC
    history.mkdir(parents=True)
    with WorkflowProgress(tmp_path / 'workflow', 'test-capture', create=True) as progress:
        for ordinal, (turn, entry) in enumerate(zip((t4, t5), (anchor(r4, 4, 'p' * 22), anchor(r5, 5, 'x' * 22)))):
            progress.plan_turn(ordinal, turn)
            entry['question_id'] = turn.id
            surface = dict(session_id=entry['session_id'], gateway_identity=[42, 123], model_requests=[],
                           write_barrier=dict(readback=entry['readback']))
            progress.verified_turn(turn, surface, {})
            progress.mark_turn_done(turn.id)
            (history / f"{entry['revision']}.json").write_text(json.dumps(entry))

        @contextmanager
        def reader(_):
            with WorkflowProgress(tmp_path / 'workflow', 'test-capture', read_only=True) as copy:
                yield copy

        monkeypatch.setattr(week, 'progress_reader', reader)
        effect = SourceEffect(UPDATE, DOC)
        for sample, revision in zip(payloads, (4, 5), strict=True):
            monkeypatch.setenv('PACBENCH_SUPERMEMORY_CHUNK_WITNESS', json.dumps(sample['witness']))
            proof = week.capture_slot(scope, effect, tmp_path, sample['body'], sample['round'])
            assert [v['revision'] for v in proof['chunk_reads']['opening']['candidates']] == [revision]
            assert [v['revision'] for v in proof['chunk_reads']['tools']['call-a']['candidates']] == [5]
        changed = deepcopy(second['body'])
        changed['messages'][1]['content'] += ' unobserved'
        with pytest.raises(OwnershipConflict, match='wire prompt'):
            week.capture_slot(scope, effect, tmp_path, changed, second['round'])
        changed_round = deepcopy(second['round'])
        changed_round['results']['call-a']['content_sha256'] = '0' * 64
        with pytest.raises(OwnershipConflict, match='lacks its own native chunk read'):
            week.capture_slot(scope, effect, tmp_path, second['body'], changed_round)
        changed = deepcopy(second['body'])
        changed['messages'][2]['tool_calls'][0]['function']['arguments'] = '{"positions":[0]}'
        with pytest.raises(OwnershipConflict, match='wire readChunks calls'):
            week.capture_slot(scope, effect, tmp_path, changed, second['round'])
        result = subprocess.run([BINARY], cwd=tmp_path, env=dict(env, AMBIGUOUS_READS='1'),
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        ambiguous = json.loads(result.stdout.splitlines()[-1])
        assert not ambiguous['witness']['tools']
        monkeypatch.setenv('PACBENCH_SUPERMEMORY_CHUNK_WITNESS', json.dumps(ambiguous['witness']))
        with pytest.raises(OwnershipConflict, match='lacks its own native chunk read'):
            week.capture_slot(scope, effect, tmp_path, ambiguous['body'], ambiguous['round'])
        monkeypatch.setenv('PACBENCH_SUPERMEMORY_CHUNK_WITNESS', json.dumps(second['witness']))
        (history / '5.json').unlink()
        with pytest.raises(OwnershipConflict, match='eligible verified capture'):
            week.capture_slot(scope, effect, tmp_path, second['body'], second['round'])


def test_identical_observed_chunks_report_both_eligible_revisions():
    observed = dict(at_ms=5, chunks=[dict(id='a' * 22, position=0, sha256='a' * 64)])
    candidates = [dict(anchor(observed['chunks'], rev, chr(64 + rev) * 22, at_ms=rev), question_id=f'q{rev}')
                  for rev in (4, 5, 6)]
    proof = chunks._candidates(observed, candidates, opening=True)
    assert [row['revision'] for row in proof['candidates']] == [4, 5]


@pytest.mark.parametrize('returned,positions', [(1, [0, 4]), (0, [4]), (1, [])])
def test_readchunks_omitted_requested_row_cannot_match_revision(returned, positions):
    rows = [dict(id=chr(97 + i) * 22, position=i, sha256='a' * 64) for i in (0, 4)]
    anchors = [dict(anchor(rows, 4, 'p' * 22), question_id='q4')]
    observed = dict(at_ms=3, chunks=rows[:returned], arguments={'positions': positions}, result_sha256='b' * 64)
    with pytest.raises(OwnershipConflict, match='eligible verified capture'):
        chunks._candidates(observed, anchors, opening=False)
