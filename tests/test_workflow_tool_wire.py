import pytest

from pacbench.workflow_runner import _tool_call_contract


def test_native_metadata_and_json_spacing_preserve_user_visible_tool_lookup():
    stored = {'id':'call-1', 'call_id':'call-1', 'response_item_id':'item-1', 'type':'function',
              'function':{'name':'memcal_search_archive', 'arguments':'{"n": 1, "query": "ƒ"}'}}
    wire = {'id':'call-1', 'type':'function',
            'function':{'name':'memcal_search_archive', 'arguments':'{"query":"\\u0192","n":1}'}}
    assert _tool_call_contract(stored, stored=True) == _tool_call_contract(wire)
    changed = {**wire, 'function':{**wire['function'], 'arguments':'{"query":"ƒ","n":true}'}}
    assert _tool_call_contract(stored, stored=True) != _tool_call_contract(changed)
    with pytest.raises(ValueError, match='aliases'):
        _tool_call_contract({**stored, 'call_id':'other'}, stored=True)
