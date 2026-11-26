"""Exercise the stock Week from the repository with python -m tests.supermemory_week_rehearsal."""
import argparse
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock, Thread
import time
from uuid import uuid4

from . import supermemory_offline_transport


REPO = Path(__file__).resolve().parents[1]
MODEL = 'openai/gpt-6-luna'
PROVIDER = {'only':['amazon-bedrock/global'], 'order':['amazon-bedrock/global'],
            'allow_fallbacks':False, 'require_parameters':True}
TOKEN = supermemory_offline_transport.TOKEN


class FixtureModel(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, data, mime='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply_json(self, data):
        self.reply(json.dumps(data).encode())

    def do_GET(self):
        assert self.path == '/api/v1/models'
        self.reply_json({'data':[{'id':MODEL, 'context_length':128000,
                                  'top_provider':{'max_completion_tokens':32768}}]})

    def do_POST(self):
        assert self.path == '/api/v1/chat/completions'
        arrived_at_ms = time.time_ns() // 1_000_000
        raw = self.rfile.read(int(self.headers['Content-Length']))
        request = json.loads(raw)
        assert request['model'] == MODEL and request['provider'] == PROVIDER
        sender = self.headers['x-offline-fixture-sender']
        operation = self.headers.get('x-offline-native-role')
        target = self.headers.get('x-offline-target-capture') == 'five-revisions'
        assert sender in ('hermes','stock-server')
        reply = dict(role='assistant',content='Synthetic offline Rivet response.')
        finish = 'stop'
        tool_id = None
        ordinal = None
        if sender == 'hermes':
            messages = request['messages']
            current = max(i for i, msg in enumerate(messages) if msg['role'] == 'user')
            fresh = current == len(messages) - 1
            with self.server.counter_lock:
                self.server.answers += int(fresh)
                ordinal = self.server.answers
            reply['content'] = (f'Offline rehearsal answer to question {ordinal}. ' +
                'The calendar notes are available for the next discussion. ' * 5).strip()
            if ordinal == 25 and fresh:
                actual = messages[current]['content']
                assert actual == self.server.correction or actual.startswith(self.server.correction+'\n\n')
                tool_id = 'call_offline_explicit_store'
                args = json.dumps({'content':'The porch list covers Saturday only.'})
                tool = dict(id=tool_id, type='function', function=dict(name='supermemory_store',arguments=args))
                reply = dict(role='assistant',content=None,tool_calls=[tool])
                finish = 'tool_calls'
            elif ordinal == 25:
                assert any(msg['role']=='tool' and msg['tool_call_id']=='call_offline_explicit_store'
                           and json.loads(msg['content'])['saved'] for msg in messages[current+1:])
            if ordinal == 24 and fresh:
                # Keep the stock Rivet scheduler active while the loopback
                # model is answering the formerly blocked question.
                time.sleep(8)
            elif ordinal <= 23 and fresh:
                time.sleep(.8)
        elif operation == 'capture' and target and not any(row['role'] == 'tool' for row in request['messages']):
            # Trigger a real readChunks result through the pinned stock worker.
            tools = {row['function']['name'] for row in request.get('tools') or []}
            with self.server.counter_lock:
                trigger = 'readChunks' in tools and not self.server.requested_target_tool
                self.server.requested_target_tool |= trigger
            if trigger:
                tool_id = 'call_offline_chunks_' + uuid4().hex[:16]
                call = dict(id=tool_id, type='function', function=dict(name='readChunks', arguments='{"positions":[0]}'))
                reply = dict(role='assistant', content=None, tool_calls=[call])
                finish = 'tool_calls'
        elif target and self.server.case == 'unresolved':
            marker = self.server.campaign / 'withheld-background.json'
            with marker.open('x') as stream:
                json.dump({'arrived_at_ms': arrived_at_ms, 'request_sha256': sha256(raw).hexdigest()}, stream)
            # Keep the sent request unresolved through the stock controller's
            # read/drain and cleanup. No response or generation ID is invented.
            result = self.server.campaign / 'supermemory-stock-week-v4/supermemory/result.json'
            until = time.monotonic() + 190
            while not result.is_file() and time.monotonic() < until:
                time.sleep(.2)
            return
        generation = 'gen-offline-supermemory-' + uuid4().hex
        usage = dict(prompt_tokens=500, completion_tokens=80, total_tokens=580, cost=0)
        envelope = dict(id=generation, created=int(time.time()), model=MODEL, provider='Amazon Bedrock')
        prompt = request['messages'][1]['content'] if operation == 'capture' else None
        canonical = json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
        with self.server.counter_lock, self.server.trace.open('a') as trace:
            json.dump(dict(sender=sender, role=operation, ordinal=ordinal, generation=generation,
                           tool_id=tool_id, input_sha256=sha256(raw).hexdigest(),
                           canonical_sha256=sha256(canonical).hexdigest(),
                           capture_prompt_sha256=sha256(prompt.encode()).hexdigest() if prompt is not None else None,
                           tool_results=sum(msg['role']=='tool' for msg in request['messages']),
                           arrived_at_ms=arrived_at_ms, response_at_ms=time.time_ns()//1_000_000),trace)
            trace.write('\n')
        if request.get('stream'):
            if reply.get('tool_calls'):
                reply['tool_calls'][0]['index'] = 0
            events = [{**envelope, 'object':'chat.completion.chunk', 'choices':[
                           dict(index=0, delta=reply, finish_reason=None)]},
                      {**envelope, 'object':'chat.completion.chunk', 'choices':[
                           dict(index=0, delta={}, finish_reason=finish)]},
                      {**envelope, 'object':'chat.completion.chunk', 'choices':[], 'usage':usage}]
            data = ''.join('data: '+json.dumps(event)+'\n\n' for event in events)+'data: [DONE]\n\n'
            return self.reply(data.encode(), 'text/event-stream')
        return self.reply_json({**envelope, 'object':'chat.completion', 'usage':usage,
                                'choices':[dict(index=0,message=reply,finish_reason=finish)]})


def run(campaign, plugin_home, case):
    from pacbench.providers.supermemory import week as candidate
    from pacbench.canary_capability import binding
    from pacbench.direct_accounting import now

    assert os.environ['OPENROUTER_API_KEY'] == TOKEN
    campaign.mkdir(mode=0o700)
    (campaign/'execution.lock').touch()
    child_bootstrap = campaign / 'test-bootstrap'
    child_bootstrap.mkdir()
    production_bootstrap = REPO / 'pacbench/direct_runtime/sitecustomize.py'
    (child_bootstrap / 'sitecustomize.py').write_text(
        'import os, runpy\n'
        'try:\n'
        '    from tests.supermemory_offline_transport import install\n'
        '    install()\n'
        f'    runpy.run_path({str(production_bootstrap)!r})\n'
        'except BaseException:\n'
        '    os._exit(78)\n')
    confirmation = campaign/'operator-fixture.json'
    confirmation.write_text(json.dumps(dict(auto_recharge='off',confirmed_at=now(),
                                             confirmed_by='offline test fixture')))
    credit = b'{"data":{"total_credits":1,"total_usage":0}}'

    def read_fixture(_, key):
        assert key == TOKEN
        return binding(confirmation), credit, dict(source='OpenRouter GET /api/v1/credits', remaining_usd='1',
                         request_started=now(), observed_at=now(), receipt_sha256=sha256(credit).hexdigest())

    candidate.verify_prepaid = read_fixture
    def catalog_fixture(*, profile):
        assert profile == 'luna-bedrock-global'

    candidate.fetch_catalog = catalog_fixture
    binary = (REPO/'vendor/supermemory-local/supermemory-server-linux-x64').resolve(strict=True)
    old_popen = subprocess.Popen
    bootstrap = REPO/'pacbench/direct_runtime/fetch.cjs'
    offline = Path(__file__).with_suffix('.cjs')

    def configure_preload(command, *args, **kwargs):
        if kwargs.get('env', {}).get('PACBENCH_DIRECT') == '1':
            environment = dict(kwargs['env'])
            environment['PYTHONPATH'] = os.pathsep.join((str(child_bootstrap), environment['PYTHONPATH']))
            kwargs['env'] = environment
        if isinstance(command,(list,tuple)) and command and Path(command[0]).resolve()==binary:
            root = Path(kwargs['cwd'])
            assert root == campaign/candidate.WEEK_RUN/'supermemory'
            assert kwargs['env']['PACBENCH_DIRECT_UPSTREAM_KEY'] == TOKEN
            config = root/'bunfig.toml'
            assert config.read_text() == 'preload = ['+json.dumps(str(bootstrap))+']\n'
            config.write_text('preload = '+json.dumps([str(offline),str(bootstrap)])+'\n')
        return old_popen(command,*args,**kwargs)

    subprocess.Popen = configure_preload
    original_inventory = candidate.StockWeekWitness.source_inventory

    def inject_foreign(witness):
        if witness.ready:
            ack = witness.sdk.add(witness.namespace, id='offline-foreign', content='Independent synthetic write.',
                                  date=witness.cutoff)
            assert ack.id == 'offline-foreign'
        return original_inventory(witness)

    if case == 'foreign':
        candidate.StockWeekWitness.source_inventory = inject_foreign
    try:
        return candidate.run(campaign, campaign, plugin_home)
    finally:
        candidate.StockWeekWitness.source_inventory = original_inventory
        subprocess.Popen = old_popen


def verify(campaign, case):
    from pacbench.observational_receipts import replay_attempts
    from pacbench.providers.supermemory.week_admission import WEEK_RUN, plan

    root = campaign / WEEK_RUN / 'supermemory'
    result = json.loads((root / 'result.json').read_text())
    data = [json.loads(line) for line in (root / 'workflow/workflow.jsonl').read_text().splitlines()]
    with (root / 'receipts.jsonl').open() as stream:
        attempts = list(replay_attempts(stream).values())
    docs, turns, _, _ = plan()
    assert result['cleanup'] == 'verified-inventory-stopped'
    verified = [r['question'] for r in data if r['phase'] == 'verified-turn']
    assert verified == [t.id for t in turns[:len(verified)]]
    if case == 'foreign':
        snapshot, = (root / 'inventory-rejections').glob('*.json')
        rejection = json.loads(snapshot.read_text())
        assert rejection['api'] == 'v3' and not verified
        assert any(r['customId'] == 'offline-foreign' for r in rejection['response']['memories'])
        assert not any(r['accounting_role'] == 'hermes' for r in attempts)
        return result
    worker_reads = [r for r in attempts if r['workflow_slot'].get('chunk_reads', {}).get('tools')]
    assert worker_reads, 'The native readChunks continuation was not exercised'
    continuation, = worker_reads
    reads = continuation['workflow_slot']['chunk_reads']
    assert [a['revision'] for a in reads['opening']['candidates']] == [5]
    assert any(c['revision'] == 5 for read in reads['tools'].values() for c in read['candidates'])
    if case == 'unresolved':
        assert (campaign / 'withheld-background.json').is_file()
        assert result['unsettled_transports'] == 1 and not continuation.get('finished')
        assert continuation['dispatch_execution'] == 'ambiguous' and not continuation.get('upstream_request_id')
        assert not continuation.get('billing_receipt') and not result['billing']['retried_attempts']
        assert attempts[-1]['id'] == continuation['id'] and result['state'] == 'incomplete'
        return result
    assert result['state'] == 'stock-week-native-complete-ungraded' and len(verified) == 49
    assert result['unsettled_transports'] == 0 and result['billing']['complete_cost_usd'] == '0'
    ready = [r['evidence'] for r in data if r['phase'] == 'readback']
    assert [(r['native_id'], r['submitted_sha256']) for r in ready] == [(d.filename, d.sha256) for d in docs]
    surfaces = {r['question']: r['surface'] for r in data if r['phase'] == 'answer-visible'}
    assert len({str(s['gateway_identity']) for s in surfaces.values()}) == 1
    saved, = surfaces['weekend-correction']['write_barrier']['explicit_writes']
    assert saved['chunk_anchor']['revision'] == 1 and saved['status'] == 'done'
    assert (root / 'explicit-tool-operations').is_dir()
    assert (root / 'explicit-ready' / (saved['native_id'] + '.json')).is_file()
    assert surfaces[turns[25].id]['source_files'] == surfaces[turns[24].id]['source_files']
    assert not list((root / 'inventory-rejections').iterdir())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('plugin_home', type=Path)
    parser.add_argument('--case', choices=('success', 'foreign', 'unresolved'), default='success')
    args = parser.parse_args()
    allowed = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TZ') if key in os.environ}
    os.environ.clear()
    os.environ.update(allowed, PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',
                      OPENROUTER_API_KEY=TOKEN, PYTHONDONTWRITEBYTECODE='1')
    from pacbench.providers.supermemory.week_admission import plan

    os.umask(0o077)
    campaign = args.campaign.resolve()
    with ThreadingHTTPServer(('127.0.0.1',0), FixtureModel) as server:
        server.case = args.case
        server.campaign = campaign
        server.counter_lock = Lock()
        server.answers = 0
        server.requested_target_tool = False
        server.correction = next(t.prompt for t in plan()[1] if t.id == 'weekend-correction')
        server.trace = campaign.with_suffix('.model.jsonl')
        os.environ['SM_OFFLINE_MODEL_ORIGIN'] = f'http://127.0.0.1:{server.server_port}'
        os.environ['SM_OFFLINE_NATIVE_TRACE'] = str(campaign.with_suffix('.native.jsonl'))
        supermemory_offline_transport.install()
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            try:
                run(campaign, args.plugin_home.resolve(strict=True), args.case)
            except (RuntimeError, ValueError, TimeoutError):
                if args.case == 'success':
                    raise
            result = verify(campaign, args.case)
            print(json.dumps({key:result[key] for key in ('state','cleanup','elapsed_seconds')}, sort_keys=True))
        finally:
            server.shutdown()
            thread.join()


if __name__ == '__main__':
    main()
