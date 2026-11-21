"""Pinned stock Dream, stock source readers and the actual receipt guard, with no external HTTP."""
from contextlib import closing
import io
import json
import re
import urllib.request

import pytest

from pacbench import direct_http, workflow_capability
from pacbench.live_dispatch import CONDITION, OwnershipConflict
from pacbench.observational_receipts import IndexedRunReceiptJournal
from pacbench.workflow_delivery import MemCalReaders
from pacbench.workflow_memcal_fixture import CHAT, fixture
from pacbench.workflow_memcal_source import MemCalSourceWitness
from pacbench.workflow_spec import compile_week
from pacbench.workflow_memcal_account import _source_messages

MODEL = 'openai/gpt-6-luna'
PROJECT = 'offline-second-look-memory'
URL = 'https://openrouter.ai/api/v1/chat/completions'
CUTOFF = '2026-10-07T21:00:00-04:00'


@pytest.fixture
def offline_wire(tmp_path, monkeypatch):
    """Only the external response and the operation lease are fixtures."""
    ledger = IndexedRunReceiptJournal(tmp_path/'synthetic-receipts.jsonl')
    slot = {'kind':'ingest', 'provider':'memcal', 'batch_id':CUTOFF,
            'checkpoint_effect':{'attempt_id':'synthetic-dream-effect'}}
    scope = {'root':str(tmp_path), 'run_id':'offline-only', 'provider':'memcal',
             'projects':{'memcal':{'memory':PROJECT,'judge':'unused-offline-judge'}}}
    monkeypatch.setattr(workflow_capability, 'load', lambda: scope)
    monkeypatch.setattr(workflow_capability, '_native_lease', lambda *_: dict(slot))
    monkeypatch.setattr(direct_http, 'live_budget', lambda **_: ledger)
    monkeypatch.setattr(direct_http, '_installed', False)
    monkeypatch.setattr(direct_http, '_install_httpx', lambda _: None)
    for key, val in {
        workflow_capability.ENV:'{}', 'PACBENCH_DIRECT':'1',
        'PACBENCH_DIRECT_UPSTREAM_KEY':'synthetic-no-network',
        'PACBENCH_DISPATCH_TRACKING':CONDITION, 'PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768',
        'PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1',
    }.items():
        monkeypatch.setenv(key,val)
    for key in ('PACBENCH_STOP_FILE','PACBENCH_OBSERVATIONAL_COHORT_STOP','PACBENCH_MEMORY_API_INPUT_TRACE_ROOT'):
        monkeypatch.delenv(key, raising=False)

    requests = []
    duplicate_rejections = []

    def simulated_endpoint(_opener, request, **_):
        assert request.full_url == URL
        assert request.get_header('Authorization') == 'Bearer synthetic-no-network'
        wire = json.loads(request.data)
        rows = ledger.snapshot()
        latest = rows[-1]
        assert latest['dispatch_execution'] == 'ambiguous'
        requests.append(wire)
        if wire.get('response_format',{}).get('json_schema',{}).get('name') == 'memcal_diff':
            with pytest.raises(OwnershipConflict, match='No automatic replay'):
                ledger.reserve(PROJECT, wire, wait=False)
            duplicate_rejections.append(latest['id'])
        suffix = wire['messages'][1]['content']
        ids = re.findall(r'^  ([0-9a-f]{6})  \S', suffix.split('List all',1)[0], re.M)
        content = {'reviewed':ids,'diffs':[]}
        payload = json.dumps({
            'id':f'offline-native-{len(requests):04}', 'model':MODEL, 'provider':'Amazon Bedrock',
            'choices':[{'message':{'content':json.dumps(content)}, 'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'cost':0}
        }).encode()
        result = io.BytesIO(payload)
        result.status = 200
        result.headers = {}
        return result

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', simulated_endpoint)
    direct_http.install()
    return ledger, slot, requests, duplicate_rejections


def configure(cfg):
    cfg.llm_provider = 'openai-compatible'
    cfg.openai_base_url = URL.removesuffix('/chat/completions')
    cfg.env['OPENAI_COMPAT_API_KEY'] = 'pbdirect-'+PROJECT
    cfg.propose_model = cfg.sweep_model = cfg.match_model = MODEL
    return cfg


def test_stock_dream_sends_an_identical_singleton_second_look_once(tmp_path, offline_wire):
    from memcal import archive, config, db

    ledger, _, requests, duplicate_rejections = offline_wire
    cfg = configure(config.Config(home=tmp_path/'isolated-native-store'))
    cfg.ensure_dirs()
    db.set_today(CUTOFF)
    try:
        with closing(db.open_db(cfg.db_path)) as conn:
            msg = archive.append(conn,channel='imessage',external_id='synthetic-invite',ts=CUTOFF,
                text='Can you come to a picnic on Friday at 6 pm?', person='Riley',
                gated=True,gate_reason='invitation')
            archive.spool_add(conn,msg,'person:Riley')
            conn.commit()
            assert MemCalReaders(conn,cfg).process()['pending_spool'] == 0
    finally:
        db.set_today(None)
    rows = ledger.snapshot()
    assert len(rows) == len(requests) == 3
    first, second, sweep = rows
    assert duplicate_rejections == [first['id'],second['id']]
    assert requests[0] == requests[1]
    assert first['request_sha256'] == second['request_sha256']
    assert first['dispatch_context']['operation_id'] != second['dispatch_context']['operation_id']
    assert 'native_work' not in first['workflow_slot']
    assert second['workflow_slot']['native_work']['kind'] == 'second-look'
    assert 'native_work' not in sweep['workflow_slot']
    assert all(row['state']=='settled' and not row.get('retry_of') and row['settled_charge_usd']=='0' for row in rows)
    assert first['upstream_request_id'] != second['upstream_request_id']
    with pytest.raises(OwnershipConflict, match='No automatic replay'):
        ledger.reserve(PROJECT, requests[0], wait=False)
    with pytest.raises(ValueError, match='Dream wave changed'):
        workflow_capability.confirm_memcal_sender(second)


def test_first_three_canonical_nights_reach_stock_dream_readiness(tmp_path, monkeypatch, offline_wire):
    from functools import partial
    from memcal import config, db
    from memcal.sources import imessage
    from pacbench.workflow_memcal import verify_memcal_mailbox

    ledger, slot, requests, _ = offline_wire
    campaign = tmp_path/'three-nights'
    home = campaign/'source-home'
    monkeypatch.setenv('HOME',str(home))
    # Reader normally resolves this constant on import. The subprocess already
    # imported stock modules for the singleton test, so bind the same test HOME.
    monkeypatch.setattr(imessage,'CHAT_DB',home/CHAT)
    _, docs, _, _ = compile_week('continuing-account')
    with fixture(campaign) as (site, upstream):
        cfg = configure(config.load(home=site.memcal_home))
        channels = ('email','imessage')
        with closing(db.connect(cfg.db_path)) as conn:
            readers = MemCalReaders(conn,cfg,site=site)
            readers.preflight(site.account,channels)
            witness = MemCalSourceWitness(conn,site,_source_messages(),docs,
                bindings_path=upstream.bindings_path,bindings_sha256=upstream.bindings_sha256,
                release=upstream.release,observe=upstream.observe)
            witness.verify_mailbox = partial(verify_memcal_mailbox,site,conn,cfg,checked={})
            assert witness.source_inventory() == []
            for cutoff, count in zip(upstream.cutoffs[:3],(39,62,89),strict=True):
                slot['batch_id'] = cutoff
                slot['checkpoint_effect']['attempt_id'] = cutoff
                witness.collection_clock(cutoff)
                db.set_today(cutoff)
                try:
                    accepted = witness.connector_acceptance(readers.collect(channels),cutoff)
                    ready = witness.connector_readback(accepted,processing=readers.process())
                finally:
                    db.set_today(None)
                assert ready['ready'] is True
                assert len(witness.collection_ids) == count
                assert witness.source_inventory() == [d.filename for d in docs if d.cutoff <= cutoff]
    rows = ledger.snapshot()
    assert rows and len(rows) == len(requests)
    assert all(row['state']=='settled' and row['settled_charge_usd']=='0' for row in rows)
    assert {row['workflow_slot']['batch_id'] for row in rows} == set(upstream.cutoffs[:3])
