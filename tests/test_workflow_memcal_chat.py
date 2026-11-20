"""Real MemCal + Hermes; scripted loopback model responses, no source deployment or grade."""
import json
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench import api_input_trace
from pacbench.checkpoint_clock import CheckpointClock
from pacbench.gateway_reuse import PersistentGateway
from pacbench.provider_versions import MEMCAL_REVISION
from pacbench.workflow_memcal_chat import MemCalChatWitness, verify_resume
from pacbench.workflow_native import WarmHermes
from pacbench.workflow_runner import Condition, captured_turn
from pacbench.workflow_spec import compile_week
from pacbench.workflow_tracks import HERMES_TOOLS
from tests.test_gateway_reuse import LocalNativeFixture, PYTHON, ROOT, native_home


def test_stock_memcal_continuing_chat_and_explicit_source_lookup(tmp_path, monkeypatch):
    source = Path(os.environ.get('PACBENCH_MEMCAL_SRC', ROOT.parent / 'memcal-stable-20261113'))
    if not PYTHON.exists() or not source.is_dir():
        pytest.skip('requires installed pinned Hermes and MemCal stable trees')
    tools = HERMES_TOOLS['memcal']
    _, _, turns, _ = compile_week('continuing-account')
    lunch = [t for t in turns if t.id in ('lunch-snapshot','lunch-refreshed')]
    assert [t.id for t in lunch] == ['lunch-snapshot','lunch-refreshed']

    def record(body):
        nonce = json.loads((clock.folder / 'active.json').read_text())['nonce']
        monkeypatch.setenv('PACBENCH_PYTHON_CLOCK_NONCE', nonce)
        api_input_trace.record('offline-driver', {'id':uuid.uuid4().hex, 'request':body})
        last = body['messages'][-1]
        if last['role'] == 'user' and lunch[1].prompt in last['content']:
            return dict(index=0,id='native-archive-lookup',type='function',
                        function=dict(name='memcal_search_archive',arguments=json.dumps({'query':'lunch'})))

    wire = LocalNativeFixture(tmp_path / 'wire', on_driver_request=record)
    try:
        home, env = native_home(tmp_path, wire.endpoint)
        (home / 'plugins/reme').unlink()
        (home / 'plugins/memcal').symlink_to(source / 'integrations/hermes/memcal', target_is_directory=True)
        config = json.loads((home / 'config.yaml').read_text())
        config['memory']['provider'] = 'memcal'
        config['security'] = {'allow_lazy_installs':False}
        (home / 'config.yaml').write_text(json.dumps(config))
        store = tmp_path / 'memcal-home'
        env.update(MEMCAL_HOME=str(store), MEMCAL_SRC=str(source), PACBENCH_MEMCAL_CONDITION='stable-0.8.11',
                   PACBENCH_WORKFLOW_CAPTURE='pizza-week-conversation-v2', TZ='America/New_York',
                   PACBENCH_API_INPUT_TRACE=str(tmp_path/'api-inputs.jsonl'), PACBENCH_TOOL_ALLOWLIST=','.join(tools))
        init = 'import os; from pathlib import Path; from memcal import config, db; c=config.Config(home=Path(os.environ["MEMCAL_HOME"])); c.ensure_dirs(); db.open_db(c.db_path).close()'
        subprocess.run([str(PYTHON), '-c', init], env=env, check=True)
        for name in ('HERMES_HOME','PACBENCH_WORKFLOW_CAPTURE','PACBENCH_API_INPUT_TRACE'):
            monkeypatch.setenv(name, env[name])
        monkeypatch.delenv('PACBENCH_REUSE_CLOCK_STATE', raising=False)
        clock = CheckpointClock(home, lunch[0].at, 'local-clock-diagnostic')
        cwd = tmp_path / 'empty'
        cwd.mkdir()
        prior = None
        observations = []
        with PersistentGateway(home, PYTHON, env, cwd=cwd, checkpoint=clock.checkpoint,
                               observation_clock=clock, provider='memcal') as owner:
            assert owner.pins['memcal'] == MEMCAL_REVISION and owner.alias is None
            witness = MemCalChatWitness(owner, lambda: [], timeout=10)
            warm = WarmHermes(owner, clock, witness.idle)
            condition = Condition('memcal','first-party-connector','continuing-account', 'Hermes','offline-model',
                                  'loopback','diagnostic','0.8.11',owner.pins['hermes_isolation_sha256'],'simulated',tools)
            for turn in lunch:
                if prior is None:
                    captured = {}
                    def interrupt_after_return(raw):
                        captured.update(raw)
                        raise InterruptedError('controller stopped after CLI returned')
                    with pytest.raises(InterruptedError, match='controller stopped'):
                        warm.ask(turn.prompt, at=turn.at, session_id=None, on_return=interrupt_after_return)
                    count = len(wire.requests)
                    trace = warm.read_return(turn.prompt, turn.at, None, captured)
                    assert len(wire.requests) == count
                else:
                    trace = warm.ask(turn.prompt, at=turn.at, session_id=prior['session_id'])
                trace['tool_boundaries'] = witness.tool_boundaries(trace['tool_results'])
                trace.update(write_barrier=witness.native_write_barrier(trace['session_id'],trace['answer']),
                             source_files=[], attempts=[])
                activity = captured_turn(trace,turn,prior,condition,[])
                assert trace['context_isolation']['state'] == trace['clock_evidence']['state'] == 'pass'
                assert activity['explicit_attempts'] == (1 if prior else 0)
                if prior:
                    assert prior['session_id'] == trace['session_id'] and prior['gateway_identity'] == trace['gateway_identity']
                    assert lunch[0].prompt in trace['tool_results'][0]['text']
                    proof = trace['tool_boundaries']['native-archive-lookup']
                    assert proof['verified_archive_lines'] and all(row['channel'] == 'agent' for row in proof['verified_archive_lines'])
                    assert trace['tool_provenance'] == [{'tool_call_id':'native-archive-lookup', 'tool':'memcal_search_archive',
                                                        'category':'archive_search','success':True}]
                else:
                    # Exercise read-only warm session reattachment, separately from any
                    # platform source or deployment assertion. No model request on resume.
                    source_proof = dict(store_path=str(store/'memcal.db'),cutoff=None,upstream_cutoff=None)
                    source_witness = SimpleNamespace(verify_source_resume=lambda progress:source_proof)
                    account = SimpleNamespace(provider='memcal',identity='fixture',frontend=warm,
                                              witness=witness,source_witness=source_witness,source_inventory=lambda:[])
                    bound = {'identity':{'account':'fixture','store':str(store)},'gateway':trace['gateway_identity']}
                    progress = SimpleNamespace(binding=bound.get, last_cutoff=lambda:None, next_ordinal=lambda:None,
                                               iter_sessions=lambda:[(turn.session,trace['session_id'])],
                                               latest_surface=lambda session:trace, get_effect=lambda phase,key:None)
                    plan = SimpleNamespace(last_turn_id=lambda logical:lunch[-1].id,
                                           check_inventory=lambda actual,cutoff: actual == [] and cutoff is None)
                    warm.starts.clear()
                    assert verify_resume(account, progress, plan)['sessions'] == 1
                    bound['gateway'] = [owner.witness['pid'],owner.witness['start_time']+1]
                    with pytest.raises(RuntimeError,match='original MemCal account and warm gateway'):
                        verify_resume(account, progress, plan)

                observations.append(dict(turn=turn.id, at=turn.at, gateway=trace['gateway_identity'],
                    session=trace['session_id'], clock=trace['clock_evidence']['state'],
                    saved_context=trace['context_isolation']['state'], memory_delivery=trace['memory_delivery'],
                    offered_tools=len(trace['offered_tools']), explicit_calls=trace['tool_provenance'],
                    readbacks=trace['tool_boundaries'], timing=trace['timing']))
                prior = trace
            warm.end_chat(prior['session_id'])
            close = witness.after_native_close(prior['session_id'])
            assert len(close['readback']['archive_ids']) == 2
            (tmp_path/'diagnostic.json').write_text(json.dumps(dict(evidence_kind='scripted-loopback-diagnostic',
                pins=owner.pins, observations=observations, native_close=close, source_records=0,
                paid_requests=0, score=None), indent=2))
    finally:
        wire.close()
