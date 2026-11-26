"""No services or model requests. Exercise native observation and ordinary receipts."""
import asyncio
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import fcntl
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from pacbench import direct_http
from pacbench.providers.honcho import daily_gate as gate
from pacbench.canary_capability import CanaryBusy, binding, sha
from pacbench.direct_accounting import MARKER
from pacbench.document_ingestion import NIGHTLY_JSON_VERSION, document_text
from pacbench.live_dispatch import CONDITION as TRACKING
from pacbench.observational_receipts import RunReceiptJournal
from pacbench.openrouter_route import CHAT_COMPLETIONS_URL, PROFILES
from pacbench.workflows.capability import ENV, PROFILE, load
from pacbench.providers.memcal.workflow import _source_messages
from pacbench.workflows.spec import instant

HOST = dict(workspace='owned', peerName='user', aiPeer='hermes')
SESSION = 'daily-uploads'
UNIT = 'representation:owned:daily-uploads:user'
LEASE = 'native-claim'
MODEL = PROFILES[PROFILE]['model']


def write(path, value):
    path.write_text(json.dumps(value))
    return binding(path)


def canonical_output(folder):
    folder.mkdir()
    records = {m['source_id']:m for m in _source_messages()}
    manifest = folder/'manifest.json'
    releases = []
    for at, ids in gate.schedule()[0]:
        path = folder/f'{at[:10]}.txt'
        # Use an existing renderer as the fixture producer. The gate consumes
        # its bytes and manifest and has no renderer of its own.
        path.write_text(document_text([records[i] for i in ids], at, version=NIGHTLY_JSON_VERSION))
        releases.append(dict(cutoff=at,file=path.name,document_sha256=sha(path),source_ids=ids))
    write(manifest,dict(documents=releases))
    return manifest


def stub_worker(pipe, root, project):
    from unittest.mock import patch

    direct_http._install_httpx(httpx)
    receipts = RunReceiptJournal(root/'receipts.jsonl')
    hits = []

    async def generation(rows, text, claim=LEASE, sdk_retry=0, native_attempt=1,
                         trace='native-trace', body='same', disconnect=False, sdk=False, stale=False,
                         check_only=False, missing_cost=False):
        file = rows[-1]
        q = file['queue']
        msgs = [SimpleNamespace(id=file['local_id'],public_id=file['public_id'],session_name=SESSION,
                     workspace_name=HOST['workspace'],peer_name='user',created_at=instant(file['at']),content=text)]
        telem = SimpleNamespace(workspace_name=HOST['workspace'],session_id=file['session_id'],
            queue_item_ids=[q['id']],source_message_ids=[file['public_id']],trace_id=trace,
            call_purpose='deriver.representation',step_seq=0,observed='user',observers=['hermes','user'])
        model_plan = SimpleNamespace(attempt=native_attempt,is_fallback=False)
        owner = SimpleNamespace(work_unit_key=UNIT,aqs_id=claim)
        manager = SimpleNamespace(worker_ownership={'worker':owner})
        request_json = dict(model=MODEL,messages=[dict(role='user',content=body)])

        def mock_forward(request):
            [r] = [r for r in receipts.snapshot() if r['workflow_slot']['at'] == file['at']]
            assert r['dispatch_execution'] == 'ambiguous' and r['honcho_daily_attempt']['queue_id'] == q['id']
            assert json.loads(request.content)['provider']['only'] == ['amazon-bedrock/global']
            hits.append(q['id'])
            if disconnect:
                raise httpx.ReadError('fixture disconnected after crossing transport')
            usage = {'prompt_tokens':3,'completion_tokens':2}
            if not missing_cost:
                usage['cost']='0.00001'
            return httpx.Response(200, json=dict(id=f'gen-offline-{q["id"]}',model=MODEL,choices=[],
                provider='Amazon Bedrock',usage=usage))

        async def run_model(*, telemetry, plan):
            async with httpx.AsyncClient(transport=httpx.MockTransport(mock_forward)) as client:
                key = MARKER + project
                if sdk:
                    from openai import AsyncOpenAI
                    with patch.object(AsyncOpenAI,'_build_request',gate.observe_build(AsyncOpenAI._build_request)):
                        api = AsyncOpenAI(api_key=key,http_client=client,max_retries=0,
                                          base_url=CHAT_COMPLETIONS_URL.removesuffix('/chat/completions'))
                        assert (await api.chat.completions.create(**request_json)).id.startswith('gen-offline-')
                    return
                def build(_client, _opts, *, retries_taken):
                    return client.build_request('POST',CHAT_COMPLETIONS_URL,json=request_json,
                                                headers={'Authorization':'Bearer '+key})
                req = gate.observe_build(build)(client,SimpleNamespace(idempotency_key=trace),retries_taken=sdk_retry)
                if check_only:
                    token = direct_http._native_http_request.set(req)
                    try:
                        direct_http.admitted(req.url,req.headers,req.content,wait=False)
                    finally:
                        direct_http._native_http_request.reset(token)
                else:
                    await client.send(req)

        async def batch(messages, config, **kwargs):
            await gate.observe(run_model,gate._executor,gate.executor)(telemetry=telem,plan=model_plan)

        async def worker(manager, unit, worker_id):
            await gate.observe(batch,gate._batch,gate.batch)(msgs,None,queue_item_ids=[q['id']],
                        queue_item_message_ids=[file['local_id']],session_id=file['session_id'],
                        observed='user',observers=['user','hermes'])

        original = RunReceiptJournal.mark_dispatch_possible
        def race(journal, ident):
            q['claim'] = 'changed-after-reservation'
            original(journal, ident)
        with patch.object(gate,'capture',lambda *_:rows):
            with patch.object(RunReceiptJournal,'mark_dispatch_possible',race if stale else original):
                await gate.observe(worker,gate._work,gate.work)(manager,UNIT,'worker')

    try:
        while command := pipe.recv():
            try:
                asyncio.run(generation(**command))
            except Exception as caught:
                pipe.send((type(caught).__name__,str(caught),list(hits)))
            else:
                pipe.send(('ok','',list(hits)))
    finally:
        pipe.close()


@pytest.fixture
def owned(tmp_path, monkeypatch, request):
    root = tmp_path/'honcho'
    (root/'hermes-home').mkdir(parents=True)
    write(root/'hermes-home/honcho.json',dict(hosts=dict(hermes=HOST)))
    for name in ('native-processing-settings','native-container-ownership'):
        write(root/(name+'.json'),{})
    verification = root/'prepaid-verification'
    verification.mkdir()
    credit = verification/'credit.json'
    credit_sha = write(credit,dict(data=dict(total_credits=4,total_usage=1)))['sha256']
    credit.rename(verification/(credit_sha+'.private.json'))
    recharge = write(root/'recharge.json',dict(auto_recharge='off',confirmed_at='2026-01-01T00:00:00+00:00',
                                               confirmed_by='offline fixture'))
    producer = tmp_path/'producer'
    compiler = getattr(request, 'param', None) == 'shared'
    manifest = Path(gate.materialize_canonical_files(producer)['path']) if compiler else canonical_output(producer)
    manifest_sha = sha(manifest)
    settings = dict(PACBENCH_DIRECT='1',PACBENCH_DIRECT_UPSTREAM_KEY='offline-placeholder',
        PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_OPENROUTER_PROFILE=PROFILE,
        PACBENCH_DISPATCH_TRACKING=TRACKING,PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
        PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1')
    for k,v in settings.items():
        monkeypatch.setenv(k,v)
    for k in ('PACBENCH_STOP_FILE','PACBENCH_OBSERVATIONAL_COHORT_STOP','PACBENCH_MEMORY_API_INPUT_TRACE_ROOT'):
        monkeypatch.delenv(k,raising=False)
    rows, acks = [], {}
    monkeypatch.setattr(gate,'capture',lambda *_:deepcopy(rows))
    with (tmp_path/gate.LOCK).open('w+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        options = dict(execution_lock=lock,session_id=SESSION,canonical_manifest=manifest,manifest_sha256=manifest_sha,
                       prepaid=dict(receipt_sha256=credit_sha,source='OpenRouter GET /api/v1/credits',remaining_usd='3'),
                       auto_recharge_evidence=recharge)
        if compiler:
            options['layout'] = gate.CANONICAL_LAYOUT
        monkeypatch.setenv(ENV,json.dumps(gate.create(root,**options)))
        plan = load()['plan']
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe()
        process = ctx.Process(target=stub_worker,args=(child,root,load()['projects']['honcho']['memory']))
        process.start()
        child.close()
        try:
            gate.register(root,process.pid)

            def upload(index):
                entry = plan[index]
                filename, raw = gate.begin(root,entry['at'])
                assert sha(Path(entry['path'])) == entry['sha256']
                assert raw.decode() == Path(entry['path']).read_text()
                public_id, local_id = f'public-{index}', 200+index
                meta = {'filename':filename}
                ack = SimpleNamespace(id=public_id,workspace_id=HOST['workspace'],session_id=SESSION,
                         peer_id='user',created_at=instant(entry['at']),content=raw.decode(),metadata=meta)
                acks[public_id] = deepcopy(ack)
                q = dict(id=300+index,session_id='stock-session-id',session=SESSION,peer='user',unit=UNIT,tenant=None,
                    observers=['hermes','user'],at=entry['at'],sha256=entry['sha256'],processed=False,failed=False,claim=LEASE)
                rows.append(dict(public_id=public_id,local_id=local_id,session_id='stock-session-id',session=SESSION,
                    peer='user',at=entry['at'],sha256=entry['sha256'],seq=index+1,meta=meta,
                    embeddings=[1,1,0],file=dict(filename=filename,file_id=f'file-{index}',chunk_index=0,
                              total_chunks=1,original_file_size=len(raw)),queue=q))
                return ack

            def invoke(**options):
                parent.send(dict(rows=rows,text=acks[rows[-1]['public_id']].content,**options))
                assert parent.poll(10)
                return parent.recv()

            session = SimpleNamespace(id=SESSION,workspace_id=HOST['workspace'],get_message=lambda mid:deepcopy(acks[mid]))
            yield SimpleNamespace(root=root,manifest=manifest,options=options,plan=plan,rows=rows,acks=acks,
                                  upload=upload,invoke=invoke,session=session)
        finally:
            parent.send(None)
            parent.close()
            process.join(3)
            if process.is_alive():
                process.terminate()
                process.join()


def seal(owned, ack, index=0):
    return gate.seal(owned.root,owned.plan[index]['at'],owned.session,[ack],timeout=0)


def test_frozen_manifest_has_39_rows_in_one_file_and_no_early_or_duplicate_upload(owned):
    with pytest.raises(gate.GateError,match='Next exact'):
        owned.upload(1)
    ack = owned.upload(0)
    rows = [json.loads(line) for line in ack.content.splitlines()]
    assert len(rows) == 39 and [r['source_id'] for r in rows] == owned.plan[0]['source_ids']
    assert {r['channel'] for r in rows} == {'email','imessage'} and len(owned.rows) == 1
    with pytest.raises(gate.GateError,match='no upload replay'):
        owned.upload(0)
    with pytest.raises(gate.GateError,match='no upload replay'):
        owned.upload(1)
    assert owned.invoke(check_only=True)[0] == 'CanaryBusy'
    q = owned.rows[0].pop('queue')
    owned.rows[0]['queue'] = None
    with pytest.raises(TimeoutError):
        seal(owned,ack)
    owned.rows[0]['queue'] = q
    seal(owned,ack)


@pytest.mark.parametrize('owned', ['shared'], indirect=True)
def test_shared_renderer_crosses_stock_upload_and_readback_but_grants_no_driver(owned):
    sdk = pytest.importorskip('honcho')
    from pacbench.workflows.capability import admission
    from pacbench.campaigns.pizza_week import compile_daily_week

    scope = load()
    _, documents, turns, _, manifest = compile_daily_week()
    first = documents[0]
    saved = owned.upload(0)
    wire = dict(id=saved.id, content=first.text, peer_id=saved.peer_id, session_id=saved.session_id,
                created_at=saved.created_at.isoformat(), workspace_id=saved.workspace_id,
                token_count=3000, metadata=saved.metadata)
    uploads = []

    def respond(req):
        assert req.url.host == 'offline.invalid'
        if req.url.path == '/v3/workspaces':
            return httpx.Response(200,json=dict(id=saved.workspace_id,created_at=wire['created_at']))
        if req.url.path == f'/v3/workspaces/{saved.workspace_id}/sessions':
            assert req.method == 'POST' and json.loads(req.content) == {'id': SESSION}
            return httpx.Response(200,json=dict(id=SESSION,is_active=True,workspace_id=saved.workspace_id,
                                                created_at=wire['created_at']))
        if req.url.path.endswith('/messages/upload'):
            message = BytesParser(policy=policy.default).parsebytes(
                b'Content-Type: '+req.headers['content-type'].encode()+b'\r\n\r\n'+req.content)
            fields = {p.get_param('name', header='content-disposition'):p for p in message.iter_parts()}
            assert fields['file'].get_filename() == first.filename
            assert fields['file'].get_payload(decode=True) == first.text.encode('utf-8')
            assert json.loads(fields['metadata'].get_content()) == saved.metadata
            assert instant(fields['created_at'].get_content()) == instant(first.cutoff)
            assert fields['peer_id'].get_content() == saved.peer_id
            uploads.append(req.method)
            return httpx.Response(201,json=[wire])
        assert req.method == 'GET' and req.url.path.endswith('/messages/'+saved.id)
        return httpx.Response(200,json=wire)

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        runtime = sdk.Honcho(environment='local',base_url='http://offline.invalid',
                             workspace_id=saved.workspace_id,max_retries=0,http_client=transport)
        session = runtime.session(SESSION)
        acknowledged = session.upload_file((first.filename,first.text.encode('utf-8'),'text/plain'),saved.peer_id,
                                           metadata=saved.metadata,created_at=first.cutoff)
        gate.seal(owned.root,first.cutoff,session,acknowledged,timeout=0)

    assert uploads == ['POST'] and len(first.source_ids) == 39
    assert json.loads(owned.manifest.read_text()) == manifest
    assert owned.invoke() == ('ok','',[300])
    owned.rows[0]['queue']['processed'] = True
    gate.finish(owned.root,first.cutoff)
    assert gate.followup_ready(owned.root,'rsvp-before') == turns[0].prompt
    driver = scope['projects']['honcho']['memory'].removesuffix('-memory')+'-driver'
    with pytest.raises(ValueError,match='unexpected workflow role'):
        admission(driver,PROFILE,dict(model=MODEL,messages=[dict(role='user',content=turns[0].prompt)]))


@pytest.mark.parametrize('problem',['cutoff','ack_body','extra_chunk','queued_body'])
def test_seal_rejects_wrong_native_readback_and_enrollment(owned, problem):
    ack = owned.upload(0)
    row = owned.rows[0]
    if problem == 'cutoff':
        row['at'] = owned.plan[1]['at']
    elif problem == 'ack_body':
        owned.acks[ack.id].content += '\nchanged'
    elif problem == 'extra_chunk':
        owned.rows.append(deepcopy(row))
    else:
        row['queue']['sha256'] = 'wrong'
    with pytest.raises(gate.GateError):
        seal(owned,ack)


def test_readiness_then_exact_natural_followup_and_next_upload(owned):
    ack = owned.upload(0)
    seal(owned,ack)
    at, later = [p['at'] for p in owned.plan[:2]]
    followup, future = [next(t for t in gate.schedule()[1] if instant(e) <= instant(t.at)) for e in (at,later)]
    with pytest.raises(gate.GateError,match='verified release prefix'):
        gate.followup_ready(owned.root,followup.id)
    row = owned.rows[0]
    row['queue']['claim'] = 'stranger'
    assert owned.invoke()[0] == 'GateError'
    row['queue']['claim'] = LEASE
    assert owned.invoke(missing_cost=True) == ('ok','',[300])
    [attempt] = RunReceiptJournal(owned.root/'receipts.jsonl').snapshot()
    assert attempt['state'] == 'receipt_missing'
    with pytest.raises(CanaryBusy,match='still pending'):
        gate.finish(owned.root,at)
    row['queue'].update(processed=True,failed=True)
    with pytest.raises(gate.GateError,match='enrollment differs'):
        gate.finish(owned.root,at)
    row['queue']['failed'] = False
    row['embeddings'] = [1,0,0]
    with pytest.raises(CanaryBusy,match='indexing.*not complete'):
        gate.finish(owned.root,at)
    row['embeddings'] = [1,0,1]
    with pytest.raises(gate.GateError,match='indexing failed'):
        gate.finish(owned.root,at)
    row['embeddings'] = [1,1,0]
    gate.finish(owned.root,at)
    assert gate.followup_ready(owned.root,followup.id) == followup.prompt
    with pytest.raises(gate.GateError,match='verified release prefix'):
        gate.followup_ready(owned.root,future.id)
    row['queue'] = None  # Successful cleanup after durable positive readiness.
    assert gate.followup_ready(owned.root,followup.id) == followup.prompt
    for i in range(1,len(owned.plan)):
        seal(owned,owned.upload(i),i)
        assert owned.invoke() == ('ok','',list(range(300,301+i)))
        owned.rows[i]['queue']['processed'] = True
        gate.finish(owned.root,owned.plan[i]['at'])
    assert len(owned.rows) == 7


def test_ambiguous_send_cannot_replay_with_new_sdk_trace_body_or_claim(owned):
    seal(owned,owned.upload(0))
    for options in ({'sdk_retry':1},{'native_attempt':2}):
        assert owned.invoke(**options)[0] == 'GateError'
    assert owned.invoke(disconnect=True)[0] == 'ReadError'
    owned.rows[0]['queue']['claim'] = 'reclaimed'
    kind, reason, hits = owned.invoke(trace='new-trace',body='different',claim='reclaimed')
    assert kind == 'OwnershipConflict' and 'No automatic replay' in reason and hits == [300]
    owned.rows[0]['queue']['processed'] = True
    with pytest.raises(gate.GateError,match='matching finished generation'):
        gate.finish(owned.root,owned.plan[0]['at'])


def test_claim_changes_immediately_before_transport(owned):
    seal(owned,owned.upload(0))
    kind, _, hits = owned.invoke(stale=True)
    assert kind == 'GateError' and hits == []
    [prepared] = RunReceiptJournal(owned.root/'receipts.jsonl').snapshot()
    assert prepared['dispatch_execution'] == 'prepared'


def test_exact_pinned_sdk_reaches_mock_transport(owned):
    sdk = pytest.importorskip('openai')
    if sdk.__version__ != '2.36.0':
        pytest.skip('Honcho 3.6.1 pins OpenAI 2.36.0')
    seal(owned,owned.upload(0))
    assert owned.invoke(sdk=True) == ('ok','',[300])


@pytest.mark.parametrize('mutation',['wrong_day','wrong_order','foreign_id','changed_bytes'])
def test_rejects_changed_layout_bytes_and_record_membership(tmp_path, mutation):
    manifest = canonical_output(tmp_path/'producer')
    raw = json.loads(manifest.read_text())
    first, second = raw['documents'][:2]
    path = manifest.parent/first['file']
    if mutation == 'wrong_day':
        first['cutoff'] = second['cutoff']
    elif mutation == 'wrong_order':
        first['source_ids'].reverse()
    elif mutation == 'foreign_id':
        path.write_text(path.read_text()+second['source_ids'][0]+'\n')
        first['document_sha256'] = sha(path)
    else:
        path.write_text(path.read_text()+'changed after the producer wrote its manifest\n')
    ref = write(manifest,raw)
    with pytest.raises(gate.GateError):
        for item in gate._plan(ref,('documents','cutoff','file','document_sha256','source_ids')):
            gate._payload(item)
