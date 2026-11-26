"""Replay checks and an offline execution of the pinned stock SDK transcript code."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

from pacbench import execution_capability
from pacbench.providers.supermemory import worker as rounds
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.supermemory.source_admission import BINARY_SHA
from pacbench.providers.supermemory.week_admission import WeekJournal

REPO = Path(__file__).resolve().parents[1]
BINARY = REPO / 'vendor/supermemory-local/supermemory-server-linux-x64'
EFFECT = {'update_id': 'u' * 22, 'document_id': 'd' * 22}
VISIT = '12345678-1a23-4b56-8c78-123456789abc'
CALL = {'id': 'call-verified', 'type': 'function',
        'function': {'name': 'readChunks', 'arguments': '{"positions": [0]}'}}


def result_hash(value):
    return sha256(value.encode()).hexdigest()


@pytest.fixture
def worker(tmp_path, monkeypatch):
    opened = [{'role': 'system', 'content': 'Frozen stock role.'}, {'role': 'user', 'content': 'Frozen input.'}]
    assistant = {'role': 'assistant', 'content': None, 'reasoning': 'offline-deliberation', 'tool_calls': [CALL]}
    tool = {'role': 'tool', 'tool_call_id': CALL['id'], 'content': '{"chunks":[]}'}
    visit = dict(visit=VISIT, results={})
    monkeypatch.setenv('PACBENCH_LUNA_COMPATIBILITY', 'temperature-omitted-default-v1')
    monkeypatch.setenv('PACBENCH_EFFECTIVE_OUTPUT_TOKENS', '32768')
    monkeypatch.delenv('PACBENCH_STOP_FILE', raising=False)

    def admit(project, profile, body):
        slot = dict(provider='supermemory', kind='ingest', batch_id='first', stock_source_effect=EFFECT,
                    worker_round=rounds.describe_request(body, visit))
        return dict(run_id='synthetic', slot=slot)

    monkeypatch.setattr(execution_capability, 'admission', admit)
    monkeypatch.setattr(execution_capability, 'fields', lambda permit: {'workflow_slot': permit['slot']})
    ledger = WeekJournal(tmp_path / 'receipts.jsonl')
    project = 'fixture-memory'
    opening = dict(model=ledger.model, messages=opened)
    prepared = ledger.prepare(project, opening)
    ledger.mark_dispatch_possible(prepared['id'])
    payload = dict(id='native-gen-1', model=ledger.model, provider='Amazon Bedrock',
                   choices=[dict(index=0, finish_reason='tool_calls', message=assistant)],
                   usage=dict(prompt_tokens=8, completion_tokens=4, cost='0.0005'))
    ledger.settle(prepared['id'], 200, json.dumps(payload), body_complete=True)
    stock_reply = {k: v for k, v in assistant.items() if k != 'reasoning'}
    stock_reply['reasoning_content'] = assistant['reasoning']
    visit['results'][CALL['id']] = dict(name='readChunks', content_sha256=result_hash(tool['content']))
    # Round evidence is obtained from the SDK separately in the pinned-Bun test.
    return ledger, project, opening, dict(model=ledger.model, messages=[*opened, stock_reply, tool]), visit


def test_settlement_atomically_enables_one_native_continuation(worker):
    ledger, project, opening, next_round, visit = worker
    assert ledger.snapshot()[0]['stock_worker_tool_response']['reply']['calls'][0]['id'] == CALL['id']
    next_id = ledger.prepare(project, next_round)['id']
    assert [r['workflow_slot']['worker_round']['ordinal'] for r in ledger.snapshot()] == [0, 1]
    assert len({r['dispatch_context']['operation_id'] for r in ledger.snapshot()}) == 2
    with pytest.raises(OwnershipConflict, match='already prepared'):
        ledger.prepare(project, next_round)
    assert ledger.snapshot()[-1]['id'] == next_id
    visit['results'].clear()  # A fresh observer context cannot replay this update's opening.
    with pytest.raises(OwnershipConflict, match='already prepared'):
        ledger.prepare(project, opening)


@pytest.mark.parametrize('fault', ['visit', 'source-prefix', 'tool-result', 'tool-arguments',
                                    'assistant-text', 'assistant-reasoning', 'sampling', 'no-result-observation'])
def test_changed_native_history_or_reply_cannot_prepare(worker, fault):
    ledger, project, _, request, evidence = worker
    request = deepcopy(request)
    if fault == 'visit':
        evidence['visit'] += 'changed'
    elif fault == 'source-prefix':
        request['messages'][1]['content'] += ' changed input'
    elif fault == 'tool-result':
        request['messages'][-1]['content'] = '{"chunks":["not executed"]}'
    elif fault == 'tool-arguments':
        request['messages'][-2]['tool_calls'][0]['function']['arguments'] = '{"positions":[5]}'
    elif fault == 'assistant-text':
        request['messages'][-2]['content'] = 'new assistant text'
    elif fault == 'assistant-reasoning':
        request['messages'][-2]['reasoning_content'] = 'changed'
    elif fault == 'sampling':
        request['top_p'] = 0.3
    else:
        evidence['results'].clear()
    with pytest.raises(OwnershipConflict):
        ledger.prepare(project, request)
    assert len(ledger.snapshot()) == 1


def stock_fragments(binary):
    """Use exact executable bytes, rather than a guessed shape for the tool output."""
    data = binary.read_bytes()
    assert sha256(data).hexdigest() == BINARY_SHA
    cuts = [
        (b'async function AY({toolCallId:', b'function y$0('),  # Tool output rendering.
        (b'async function ei8({content:', b'function Hi8('),  # Result and assistant transcript.
        (b'function cZ(z){var W,v;return', b'function yb8('),  # OpenAI-compatible options.
        (b'function rb8(z){var W,v,V;', b'function X10('),  # Actual HTTP message conversion.
    ]
    fragments = []
    for start, end in cuts:
        assert data.count(start) == 1
        p = data.index(start)
        stop = data.index(end, p)
        assert stop - p < 4000
        fragments.append(data[p:stop].decode())
    return '\n'.join(fragments)


def test_pinned_bun_observes_stock_tool_before_wire_conversion(tmp_path, worker):
    if not BINARY.exists():
        pytest.skip('pinned Supermemory server is unavailable')
    ledger, project, opening, _, evidence = worker
    observer = REPO / 'pacbench/direct_runtime/supermemory_source_effect.cjs'
    native = tmp_path / 'sdk.js'
    native.write_text(stock_fragments(BINARY))
    probe = tmp_path / 'probe.cjs'
    probe.write_text('''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const {AsyncLocalStorage} = require('node:async_hooks');
// This preload exits before the application starts and never opens a socket.
globalThis.fetch = () => { throw Error('test prohibits network'); };
const source = fs.readFileSync(process.env.ROUND_OBSERVER, 'utf8');
const fragments = fs.readFileSync(process.env.ROUND_SDK, 'utf8');
const stock = vm.runInThisContext(`(function(){${fragments}\nreturn {ei8,rb8}})()`,
  {filename:'/$bunfs/root/supermemory-server'});
const capture = {exports:{}};
// Seed only the outer Rivet visit fixture. The production output observer and
// pinned functions still execute in Bun and supply the actual result digest.
const seed = vm.runInThisContext(`(function(require,module){${source}
  return (storage, token, context) => contexts.set(storage, new WeakMap([[token, context]]));
})`, {filename:process.env.ROUND_OBSERVER})(require, capture);
const storage = new AsyncLocalStorage(), token = {};
const active = {update:'u'.repeat(22), document:'d'.repeat(22),
  visit:'12345678-1a23-4b56-8c78-123456789abc', results:new Map()};
seed(storage, token, {active, ambiguous:false});
storage.run(token, async () => {
 const input = {positions:[0]};
 const call = {toolCallId:'call-verified', toolName:'readChunks', input};
 const tool = {execute: async () => ({chunks:[]})};
 const step = await stock.ei8({content:[{type:'reasoning',text:'offline-deliberation'},
   {type:'tool-call',...call}, {type:'tool-result',...call,output:await tool.execute(input)}],
   tools:{readChunks:tool}});
 const proof = capture.exports.roundWitness();
 assert.equal(Object.keys(proof.results).length, 1);
 const wire = stock.rb8(step);
 assert.equal(wire[1].tool_call_id, 'call-verified');
 await stock.ei8({content:[{type:'tool-result',...call, output:{chunks:['changed']}}], tools:{readChunks:tool}});
 assert.throws(() => capture.exports.roundWitness(), /No unique native/);
 console.log(JSON.stringify({proof,wire}));
 process.exit(0);
}).catch(error => {console.error(error);process.exit(1);});
''')
    (tmp_path / 'bunfig.toml').write_text('preload = [' + json.dumps(str(probe)) + ']\n')
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LANGUAGE', 'LC_ALL')}
    env.update(HOME=str(tmp_path), ROUND_OBSERVER=str(observer), ROUND_SDK=str(native),
               PACBENCH_SUPERMEMORY_STOCK_ROUNDS='1')
    result = subprocess.run([str(BINARY)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout.strip())
    evidence.update(captured['proof'])
    assert captured['proof']['results'] == {CALL['id']: {'name': 'readChunks', 'content_sha256': result_hash('{"chunks":[]}')}}
    assert ledger.prepare(project, {**opening, 'messages': [*opening['messages'], *captured['wire']]})
