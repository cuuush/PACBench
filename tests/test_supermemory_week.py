from contextlib import contextmanager
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from pacbench import supermemory_week_admission as week
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.supermemory_week import StockWeekWitness
from pacbench.supermemory_week_observation import stock_temporal_metadata
from pacbench.workflow_daily_documents import compile_daily_week
from pacbench.workflow_delivery import submit_document
from pacbench.workflow_progress import WorkflowProgress


def test_stock_sdk_add_and_readback_of_one_complete_day(tmp_path):
    sdk = pytest.importorskip('supermemory')
    doc = compile_daily_week()[1][0]
    namespace, native = 'week', 'D' * 22
    wire = dict(type='text', id=doc.filename, content=doc.text[:-1], metadata=stock_temporal_metadata(doc.cutoff),
                system=dict(status='done', createdAt=doc.cutoff, updatedAt=doc.cutoff))
    db_row = dict(id=native, customId=doc.filename, containerTags=[namespace], content=wire['content'],
                  status='done', createdAt=doc.cutoff, updatedAt=doc.cutoff, metadata=wire['metadata'], latestRevision=1)
    calls = []

    def respond(req):
        calls.append(req.method)
        if req.url.path == '/ns/week/document':
            assert req.method == 'POST'
            assert json.loads(req.content) == dict(id=doc.filename, content=doc.text, date=doc.cutoff)
            return httpx.Response(200, json=dict(id=doc.filename, status='queued'))
        if req.url.path == '/ns/week/list/documents':
            return httpx.Response(200, json=dict(documents=[wire], chunks=[], memories=[],
                pagination=dict(currentPage=1, totalPages=1, totalItems=1)))
        assert req.method == 'GET' and req.url.path == '/ns/week/document/' + doc.filename
        return httpx.Response(200, json=wire)

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = sdk.Supermemory(api_key='fixture', base_url='http://offline.invalid', httpx_client=transport, max_retries=0)
        (tmp_path / 'source-membership').mkdir()
        witness = StockWeekWitness.__new__(StockWeekWitness)
        witness.root, witness.namespace, witness.endpoint, witness.sdk = tmp_path, namespace, 'http://offline.invalid', client
        witness.documents, witness.batch, witness.cutoff = [doc], [doc], doc.cutoff
        witness.ready, witness.accepted, witness.captures = {}, {}, {}
        witness.read_native = lambda path, body=None: (dict(memories=[db_row], pagination=dict(currentPage=1, totalPages=1))
                                                        if path.endswith('/list') else db_row)
        accepted = witness.acceptance(submit_document('supermemory', client.with_raw_response, namespace, doc), doc)
        proof = witness.document_readback(doc, accepted)
        assert proof['readback']['native_id'] == native and proof['submitted_sha256'] == doc.sha256
        assert proof['ready'] and proof['readback']['native_revision'] == 1
        assert witness.source_inventory() == [doc.filename]
        assert calls == ['POST', 'GET', 'POST', 'POST']  # One add and read-only processing/inventory calls.


def test_next_followup_binds_durable_chat_and_exact_day(monkeypatch, tmp_path):
    docs, turns, _, _ = week.plan()
    first, _, _, followup = turns[:4]
    assert first.session == followup.session
    scope = dict(root=str(tmp_path), controller=week.process_identity(), namespace='native-week')
    (tmp_path / 'answer-admissions').mkdir()
    (tmp_path / 'ready-inventory.json').write_text(json.dumps(dict(namespace='native-week', sources=[
        dict(name=docs[0].filename, sha256=docs[0].sha256, cutoff=docs[0].cutoff)])))
    with WorkflowProgress(tmp_path / 'workflow', 'test', create=True) as progress:
        for idx, turn in enumerate((first, followup)):
            progress.plan_turn(0 if idx == 0 else 3, turn)
        progress.effect('answer', first.id, dict(at=first.at, prompt=first.prompt, session_id=None), lambda: {'native': True}, role='driver')
        surface = dict(session_id='same-hermes', gateway_identity=[42, 123], history=[], conversation_times=[first.at], model_requests=[])
        progress.verified_turn(first, surface, {})
        progress.mark_turn_done(first.id)

        @contextmanager
        def reader(_):
            with WorkflowProgress(tmp_path / 'workflow', 'test', read_only=True) as observer:
                yield observer

        monkeypatch.setattr(week, 'progress_reader', reader)

        def continuation():
            with pytest.raises(OwnershipConflict, match='native session'):
                week.prepare_driver(scope, followup.prompt, followup.at, 'different-hermes')
            week.prepare_driver(scope, followup.prompt, followup.at, 'same-hermes')
            with pytest.raises(FileExistsError):
                week.prepare_driver(scope, followup.prompt, followup.at, 'same-hermes')
            return {'native': True}

        result = progress.effect('answer', followup.id, dict(at=followup.at, prompt=followup.prompt,
                                 session_id='same-hermes'), continuation, role='driver')
        assert result == {'native': True}
        with pytest.raises(OwnershipConflict, match='active durable'):
            week.active_answer(scope)


def test_final_exact_answer_is_independent_of_a_missing_terminal_marker():
    response = dict(http_status=200, finished='now', response_validation=dict(finish_reasons=['stop'], envelope_errors=[],
        wire_parse_errors=0, assistant_text_sha256=sha256(b'Synthetic answer.').hexdigest(), sse_done_observed=False))
    assert week.verified_final_response(response, 'Synthetic answer.')
    assert not week.verified_final_response(response, 'Different answer.')
