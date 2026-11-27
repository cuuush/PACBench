import json

from types import SimpleNamespace

import pytest

from pacbench.judging import overlays as sc

@pytest.mark.parametrize('tool,valid',[('honcho_search',True),('terminal',False)])
def test_native_retry_observer_repair_still_enforces_actual_tools(tmp_path,monkeypatch,tool,valid):
    from pacbench import context_isolation as clock_context, context_isolation as api_input_trace, provider_clock as gateway_python_clock
    (tmp_path/'provider-clock.json').write_text('{}')
    (tmp_path/'tool-policy.json').write_text(json.dumps({'allowed_tools':['honcho_search'],'source_argument_rules':{}}))
    monkeypatch.setattr(clock_context,'delivery',lambda *a,**kw:{'state':'pass','native_network_continuation_api_input_ids':['retry']})
    monkeypatch.setattr(api_input_trace,'input_rows',lambda *a:[{'request_id':'retry','turn_nonce':'nonce'}])
    monkeypatch.setattr(gateway_python_clock,'audit_witness',lambda *a:None)
    t=SimpleNamespace(policy_blocks=1,clock_context={'state':'fail','reason':'Scenario clock or natural question differs at HTTP boundary'},
        context_isolation={'state':'pass'},hermes_gateway={'turn_nonce':'nonce'},tool_calls=[{'name':tool,'input':{}}],answer='Original answer')
    if valid:
        repaired=sc.normalize_native_continuation(tmp_path,t)
        assert repaired.policy_blocks==0 and repaired.answer=='Original answer'
    else:
        with pytest.raises(ValueError,match='Actual tool policy violation'):sc.normalize_native_continuation(tmp_path,t)
