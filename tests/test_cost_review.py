"""Synthetic cost-window accounting, not provider rankings."""
from pacbench.cost_review import review,summarize
from pacbench.followups import save


def call(id,role,state='settled',cost=.01):
    return {'id':id,'role':role,'state':state,'accounted_upper_bound_usd':cost,
            'input_tokens':10 if state=='settled' else None,'output_tokens':2 if state=='settled' else None}


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
    assert row['overlapping_phase_request_ids']=={}


def test_unknown_or_unexecuted_cost_is_not_free(tmp_path):
    assert summarize([None])['settled_upper_bound_usd'] is None
    save(tmp_path/'summary.json',{'input_hash':'synthetic','results':[{'provider':'blocked','repetition':1,
        'state':'preflight-failed','reason':'Native import failed'}]})
    row=review(tmp_path,ledger=tmp_path/'absent-ledger')['providers'][0]
    assert row['cost'] is None and row['complete_for_cost_comparison'] is False
