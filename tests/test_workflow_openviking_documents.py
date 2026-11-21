"""Native OpenViking readback and tool-provenance guards without any service."""
from types import SimpleNamespace
import json

import pytest

from pacbench.workflow_native import tool_provenance
from pacbench.workflow_openviking import OpenVikingWitness, uri_rows
from pacbench.workflow_spec import compile_week


def test_native_resources_refuses_truncation_and_future_canonical_names(tmp_path):
    _, docs, _, _ = compile_week('continuing-account')
    first = docs[0]
    future = docs[-1]
    row = {'uri':'viking://resources/'+first.filename}
    page = {'status':'ok','has_more':True,'result':[row]}
    client = SimpleNamespace(get=lambda *a,**k:page)
    with pytest.raises(RuntimeError,match='resource tree is incomplete'):
        uri_rows(client,'viking://resources')
    page['has_more'] = False
    witness = OpenVikingWitness(tmp_path,None,client,None,docs[:1],docs,None)
    witness.sources[first.filename] = (row['uri'],row['uri'])
    assert witness.source_inventory() == [first.filename]
    page['result'].append({'uri':'viking://resources/unexpected/'+future.filename})
    with pytest.raises(RuntimeError,match='unreleased benchmark source'):
        witness.source_inventory()


def test_native_full_read_fallback_is_source_evidence():
    calls = [{'tool_call_id':'lookup','name':'viking_read','input':{'uri':'viking://resources/something','level':'overview'}}]
    raw = {'resolved_uri':'viking://resources/something','level':'overview','content':'raw input'}
    memory = {**raw, 'resolved_uri':'viking://user/default/memories/fact.md', 'fallback':'content/read'}
    for item,expected in [(raw,'source_summary'),(memory,'memory_detail'),
                          ({**raw,'fallback':'content/read'},'source_evidence'),
                          ({'results':[memory,{**raw,'level':'full'}]},'source_evidence')]:
        result = [{'success':True,'text':json.dumps(item)}]
        assert tool_provenance(calls,result)[0]['category']==expected
