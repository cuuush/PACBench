import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from pacbench.workflows import document_canary as canary
from pacbench.workflows.tracks import HERMES_TOOLS, MEMOS_OSS_TOOLS, MEMOS_OSS_VERSION


@pytest.mark.parametrize(('provider', 'version'), [
    ('cognee', '1.6.8 / Hermes 1.4.3'), ('openviking', '0.5.4.1'),
    ('mem0', 'OSS 2.2.1'), ('memos', MEMOS_OSS_VERSION),
])
def test_shared_audit_uses_one_journal_read_and_preserves_retained_shapes(tmp_path, monkeypatch, provider, version):
    root = tmp_path/provider
    journal = root/'workflow/workflow.jsonl'
    journal.parent.mkdir(parents=True)
    spec_sha = canary.sha(canary.SPEC)
    documents, turns, frozen = canary.schedule(spec_sha, True)
    checkout = Path(canary.__file__).resolve().parents[2]
    revision = next(line.split('=', 1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                    if line.startswith('HERMES_PIN='))
    tools = list(MEMOS_OSS_TOOLS if provider == 'memos' else HERMES_TOOLS[provider])
    profile = canary.PROFILES[canary.PROFILE]
    condition = dict(provider=provider, version=version, track='nightly-document',
                     state='continuing-account', frontend='Hermes '+revision, model=profile['model'],
                     model_route=profile['endpoint'], deployment='offline fixture', service_clock='fixture',
                     tool_names=tools, gateway_patch=canary.sha(checkout/'patches/hermes-isolated.diff'))
    if provider == 'mem0':
        del condition['service_clock']
    deployment = dict(synthetic_source_provenance='offline fixture', observed_version=condition['version'],
                      store_identity='fixture', frontend_identity=condition['frontend'], account=str(root),
                      observed_patch=condition['gateway_patch'], granted_tools=tools,
                      effective_model_route=condition['model_route'])
    schedule = [dict(id=turn.id, measure=turn.measure, criteria=len(turn.claims)) for turn in turns]
    events = [dict(phase='spec', canary=True, schedule=schedule),
              dict(phase='condition', condition=condition, deployment_witness=deployment)]
    for doc in documents:
        events.extend([dict(phase='submit'), dict(phase='readback', evidence={
            'native_id': doc.filename, 'submitted_sha256': doc.sha256,
            'ready': 'ready' if provider == 'mem0' else True})])
    for turn in turns:
        surface = dict(session_id=turn.session, history=[{'role': 'user'}], tool_calls=[], tool_results=[])
        activity = dict(explicit_attempts=0, repeat_requests=0, driver_receipts={'coverage': 'complete'})
        events.extend([dict(phase='dispatch', question=turn.id),
                       dict(phase='answer-visible', question=turn.id, surface=surface),
                       dict(phase='verified-turn', question=turn.id, measure=turn.measure, activity=activity),
                       dict(phase='judge-started', question=turn.id),
                       dict(phase='grade', question=turn.id, criteria=[{'verdict': 'missed'} for _ in turn.claims])])
    if provider != 'mem0':
        events.extend([dict(phase='chat-closed', cleanup_s=0) for _ in range(3)])
    events.append(dict(phase='completion'))
    journal.write_text(''.join(json.dumps(dict(row, elapsed_s=index))+'\n' for index, row in enumerate(events)))
    scope = dict(root=str(root), canary=True, spec_sha256=spec_sha, frozen_inputs=frozen, run_id='fixture')
    if provider != 'memos':
        scope['provider'] = provider
    if provider != 'openviking':
        scope['admission'] = canary.DOCUMENT_ARMS[provider].admission
    (root/'workflow-scope.json').write_text(json.dumps(scope))
    billing = {'complete_cost_usd': '0.01'}
    result = dict(canary=True, state='live-canary-complete', cleanup='complete',
                  workflow=canary.summarize(journal), billing=billing)
    if provider != 'mem0':
        result['provider'] = provider
    (root/'result.json').write_text(json.dumps(result))
    receipts = [{'accounting_role': role, 'upstream_request_id': role} for role in ('hermes', 'judge', 'memory_provider')]
    monkeypatch.setattr(canary, 'RunReceiptJournal', Mock(return_value=Mock(snapshot=Mock(return_value=receipts))))
    monkeypatch.setattr(canary, 'receipt_summary', Mock(return_value=billing))
    opened = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == journal:
            opened.append(self)
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, 'open', tracking_open)
        audit = canary.audit_document_canary(root, provider)
    assert opened == [journal]
    assert audit.events[-1]['phase'] == 'completion'
    assert audit.bindings['scope'] == canary.binding(root/'workflow-scope.json')
    canary.RunReceiptJournal.return_value.snapshot.assert_called_once()

    with journal.open('ab') as stream:
        stream.write(b'{')
    with pytest.raises(ValueError, match='complete terminal event'):
        canary.audit_document_canary(root, provider)
