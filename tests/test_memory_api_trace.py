import json
import pytest
from pacbench.api_input_trace import record_memory


def test_native_payload_trace_is_private_run_bound_and_does_not_store_key(tmp_path,monkeypatch):
    root=tmp_path/'component';folder=root/'repeat-1/cognee/memory-api-inputs'
    monkeypatch.setenv('PACBENCH_IMPORT_ROOT',str(root));monkeypatch.setenv('PACBENCH_MEMORY_API_INPUT_TRACE_ROOT',str(folder))
    monkeypatch.setenv('OPENROUTER_API_KEY','PRIVATE-KEY-SENTINEL')
    receipt={'id':'native-request','request':{'model':'openai/gpt-6-luna','messages':[{'role':'user','content':'conversation with four messages'}]}}
    record_memory('unit-memory',receipt)
    p=folder/'native-request.json';saved=json.loads(p.read_text())
    assert saved['request']==receipt['request'] and 'PRIVATE-KEY-SENTINEL' not in p.read_text()
    assert p.stat().st_mode&0o777==0o600
    monkeypatch.setenv('PACBENCH_MEMORY_API_INPUT_TRACE_ROOT',str(tmp_path/'outside'))
    with pytest.raises(ValueError,match='run-owned'):record_memory('unit-memory',receipt)
    assert not (tmp_path/'outside').exists()
