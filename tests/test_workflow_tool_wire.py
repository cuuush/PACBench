import pytest

from pacbench.workflows.turn_evidence import _tool_call_contract, _wire_tool_text


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


def test_tool_wire_accepts_only_removal_of_terminal_line_breaks():
    assert _wire_tool_text('row 1\nrow 2\r\n', 'row 1\nrow 2')
    assert not _wire_tool_text('row 1\nrow 2\n', 'row 1')
    assert not _wire_tool_text('row 1\nrow 2\n', 'row 1 row 2')
    assert not _wire_tool_text('row 1\n', 'row 1 ')
