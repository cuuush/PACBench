"""The upgraded display must not relabel direct actual costs as estimates."""
from copy import deepcopy
from pacbench.usage import direct_call,summarize_calls


def test_direct_settlement_keeps_actual_decimal_and_pending_full_bound():
    rows=[{'id':'known','state':'settled','accounted':'0.000000123456','accounted_upper_bound_usd':0.000000123456,
           'input_tokens':5,'output_tokens':4},
          {'id':'unknown','state':'uncertain','accounted':'0.17','accounted_upper_bound_usd':0.17,
           'input_tokens':None,'output_tokens':None}]
    original=deepcopy(rows)
    projected=[direct_call(row,'memory_provider') for row in rows]
    assert projected[0]['actual_cost_usd_decimal']=='0.000000123456'
    assert projected[0]['actual_cost_source']=='openrouter.usage.cost'
    assert projected[1]['actual_cost_usd'] is None
    totals=summarize_calls(projected)
    assert totals['actual_cost_known_usd']==0.000000123456
    assert totals['actual_cost_usd'] is None and totals['actual_cost_unknown_calls']==1
    assert totals['unconfirmed_budget_bound_usd']==0.17
    assert totals['token_price_estimate_usd']==0
    assert rows==original


def test_direct_zero_cost_is_known_and_not_an_unpriced_settlement():
    call=direct_call({'state':'settled','accounted':'0','accounted_upper_bound_usd':0,
                     'input_tokens':0,'output_tokens':0},'hermes')
    assert summarize_calls([call])['actual_cost_usd']==0


def test_historical_direct_cost_review_keeps_reported_charge_without_ledger_rewrite():
    from pacbench.cost_review import refreshed_calls
    from pacbench.direct_accounting import VERSION
    rows=[{'id':'old','transport':VERSION,'state':'settled','role':'hermes','accounted':'0.00123456789',
           'accounted_upper_bound_usd':0.00123456789,'input_tokens':3,'output_tokens':2}]
    before=deepcopy(rows);projected=list(refreshed_calls(rows,None).values())
    assert summarize_calls(projected)['actual_cost_usd']==0.00123456789
    assert summarize_calls(projected)['token_price_estimate_usd']==0
    assert rows==before
