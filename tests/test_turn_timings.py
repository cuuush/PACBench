import json
from pacbench.workflows.recording import gateway_events, measure_turn as measure, turn_timing_summary as summary, turn_timestamp as stamp


def turn(**extra):
    return {'returncode':0,'answer':'Synthetic answer','latency_s':60,'native_result_duration_s':8,
        'hermes_gateway':{'real_started_at':'2026-11-15T15:00:00+00:00','real_finished_at':'2026-11-15T15:01:00+00:00'},**extra}


def test_answer_ready_is_separate_from_native_capture_and_shutdown():
    events=gateway_events('2026-11-15 10:00:20,000 INFO response ready: synthetic\n'
        '2026-11-15 10:00:21,000 WARNING Shutdown context: signal=SIGTERM\n','America/New_York')
    row=measure(turn(),events)
    assert row['answer_ready_latency_s']==20 and row['post_answer_cleanup_s']==40
    assert row['shutdown_cleanup_s']==39 and row['runner_wall_s']==60 and row['native_reported_turn_s']==8
    assert row['answer_ready_evidence']=='native-gateway-response-ready-log'


def test_missing_or_ambiguous_logs_do_not_turn_cleanup_into_zero():
    assert measure(turn())['answer_ready_latency_s'] is None
    events=[(stamp('2026-11-15T15:00:20+00:00'),'answer_ready'),(stamp('2026-11-15T15:00:21+00:00'),'answer_ready')]
    assert measure(turn(),events)['post_answer_cleanup_s'] is None
    g=turn()['hermes_gateway'];g['real_cli_result_received_at']='2026-11-15T15:00:25+00:00'
    row=measure(turn(hermes_gateway=g));assert row['answer_ready_latency_s']==25
    assert 'upper bound' in row['answer_ready_evidence']


def test_timeout_late_answer_and_unmeasured_turns_stay_unavailable():
    events=gateway_events('2026-11-15 10:00:20,000 INFO response ready: late\n','America/New_York')
    assert measure(turn(timed_out=True),events)['answer_ready_latency_s'] is None
    assert summary([measure(turn())])['answer_ready_latency_s']['count']==0


def test_report_uses_retained_logs_without_changing_artifacts(tmp_path):
    from pacbench.workflows.recording import scored_report as build
    r=tmp_path/'run';c=r/'reme';d=c/'repeat-1/reme/turns/q';d.mkdir(parents=True)
    (r/'cohort.json').write_text(json.dumps({'providers':['reme'],'state':'complete','questions':42,'criteria':188}))
    (c/'resolved-spec.json').write_text('{"timezone":"America/New_York"}')
    log=c/'repeat-1/reme/hermes-home/logs/gateway.log';log.parent.mkdir(parents=True)
    log.write_text('2026-11-15 10:00:20,000 INFO response ready: synthetic\n')
    t={**turn(),'tool_calls':[],'tool_results':[]};f=d/'turn.json';f.write_text(json.dumps(t));before=f.read_bytes()
    ledger=tmp_path/'ledger.json';ledger.write_text('{"calls":[]}')
    value=build(r,ledger)['providers'][0]['turn_lifecycle_timing']
    assert value['summary']['answer_ready_latency_s']['mean']==20
    assert value['summary']['post_answer_cleanup_s']['mean']==40
    assert f.read_bytes()==before
