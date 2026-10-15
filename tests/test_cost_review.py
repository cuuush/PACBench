"""Synthetic cost-window accounting, not provider rankings."""
import sqlite3

from pacbench.cost_review import processing_debt,review,summarize
from pacbench.followups import save


def call(id,role,state='settled',cost=.01):
    return {'id':id,'role':role,'state':state,'accounted_upper_bound_usd':cost,
            'input_tokens':10 if state=='settled' else None,'output_tokens':2 if state=='settled' else None}


def test_turn_usage_refreshes_settlements_without_adding_background_calls_or_erasing_holds():
    from copy import deepcopy
    from pacbench.usage import refresh_window,window
    held=call('answer','memory_provider','reserved',.1)
    unknown=call('unknown','memory_provider','uncertain',.2)
    captured=window({'state':'captured','at':'before','calls':[]},
        {'state':'captured','at':'after','calls':[held,unknown]})
    original=deepcopy(captured)
    settled=call('answer','memory_provider',cost=.001)
    latest={'state':'captured','at':'later','calls':[
        settled,unknown,call('later-background','memory_provider',cost=.04)]}
    updated=refresh_window(captured,latest)
    assert captured==original
    assert updated['started']=='before' and updated['finished']=='after'
    assert updated['settlement_observed_at']=='later'
    assert [c['id'] for c in updated['model_calls']]==['answer','unknown']
    role=updated['roles']['memory_provider']
    assert role['model_calls']==2 and role['unsettled_calls']==1
    assert role['accounted_upper_bound_usd']==.201
    assert role['input_tokens'] is None  # Unknown request remains unknown.
    assert updated['per_tool_tokens'] is None
    assert refresh_window(captured,{'state':'unavailable'}) is None


def test_native_costs_count_unique_requests_and_keep_background_and_holds(tmp_path):
    save(tmp_path/'summary.json',{'input_hash':'synthetic','results':[{'provider':'local','repetition':1,'state':'diagnostic'}],
        'ingestion_schedule':[{'id':'day'}]})
    save(tmp_path/'evaluation-plan.json',{'checks':[{'id':'q'}]})
    arm=tmp_path/'repeat-1/local'
    requests=[call('ingest','memory_provider'),call('answer','hermes'),call('background','memory_provider','uncertain',.1)]
    save(arm/'model-usage.json',{'state':'captured','calls':requests})
    save(arm/'ingestion/day.json',{'ready':True,'latency_s':2,'usage':{'model_calls':[requests[0],requests[0]]}})
    save(arm/'turns/q/usage.json',{'model_calls':[requests[1]]})
    save(arm/'evaluation.json',{'checks':[{'verdict':'pass','recovery':{'score':{'points':1,'max_points':1}}}],
        'counts':{'pass':1,'fail':0,'ungraded':0}})
    row=review(tmp_path,ledger=tmp_path/'absent-ledger')['providers'][0]
    assert row['cost']['memory_provider']['model_requests']==2
    assert row['cost']['memory_provider']['settled_upper_bound_usd']==.01
    assert row['cost']['memory_provider']['held_usd']==.1
    assert row['cost']['memory_provider']['input_tokens'] is None
    assert row['phases']['ingestion']['memory_provider']['model_requests']==1
    assert row['phases']['outside_captured_windows']['memory_provider']['model_requests']==1
    assert row['complete_for_cost_comparison'] is True
    assert row['lifecycle_cost_complete'] is None
    assert row['deferred_processing']['state']=='unobserved'
    assert row['overlapping_phase_request_ids']=={}
    save(arm/'evaluation.json',{'checks':[{'verdict':'pass','recovery':{'state':'invalid'}}],
        'counts':{'pass':1,'fail':0,'ungraded':0}})
    incomplete=review(tmp_path,ledger=tmp_path/'absent-ledger')['providers'][0]
    assert incomplete['initial_counts']['pass']==1
    assert incomplete['complete_for_cost_comparison'] is False


def test_unknown_or_unexecuted_cost_is_not_free(tmp_path):
    assert summarize([None])['settled_upper_bound_usd'] is None
    save(tmp_path/'summary.json',{'input_hash':'synthetic','results':[{'provider':'blocked','repetition':1,
        'state':'preflight-failed','reason':'Native import failed'}]})
    row=review(tmp_path,ledger=tmp_path/'absent-ledger')['providers'][0]
    assert row['cost'] is None and row['complete_for_cost_comparison'] is False


def test_pending_native_capture_is_not_a_zero_future_cost(tmp_path):
    database=tmp_path/'memcal-home/memcal.db';database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript('CREATE TABLE archive(id INTEGER,channel TEXT); '
            'CREATE TABLE spool(archive_id INTEGER,processed_at TEXT); '
            "INSERT INTO archive VALUES (1,'imessage'),(2,'agent'),(3,'agent'); "
            "INSERT INTO spool VALUES (1,'done'),(2,NULL),(3,NULL);")
    debt=processing_debt(tmp_path,'memcal')
    assert debt['state']=='observed-pending'
    assert debt['pending_by_channel']=={'agent':2}
    assert debt['future_model_cost_usd'] is None
    # Portable evidence keeps the observation without exporting the private DB.
    database.unlink()
    assert processing_debt(tmp_path,'memcal')==debt


def test_replaced_attempt_cost_does_not_disappear_or_blend_into_selected_arm(tmp_path):
    save(tmp_path/'summary.json',{'input_hash':'synthetic','results':[],
        'other_attempts':[{'provider':'local','state':'interrupted','evidence':'attempts/old/local'}]})
    old=tmp_path/'attempts/old/local'
    save(old/'model-usage.json',{'state':'captured','calls':[call('before-restart','memory_provider','uncertain',.1)]})
    save(old/'ingestion/one.json',{'ready':True})
    report=review(tmp_path,ledger=tmp_path/'absent')
    assert report['providers']==[]
    attempt=report['other_attempts'][0]
    assert attempt['daily_batches_ready']==1
    assert attempt['cost']['memory_provider']['held_usd']==.1
