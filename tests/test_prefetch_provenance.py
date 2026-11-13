import pytest

from pacbench.followups import tool_profile
from pacbench.harness import TurnResult
from pacbench.prefetch_provenance import VERSION, observe


def turn(state):
    t=TurnResult(prompt='question',at='',answer='answer',tool_calls=[],tool_results=[],
        policy_blocks=0,latency_s=1,returncode=0,stderr_tail='',raw_events=1,
        prefetch_context='actual native hybrid text')
    t.prefetch_provenance=observe({'protocol':VERSION,'provider':'cognee','state':state,
        'reason':'Native retrieval source path witness'},t.prefetch_context)
    return t


@pytest.mark.parametrize('state,source,unknown',[('source',True,0),('unverified',False,1)])
def test_prefetch_counts_provenance_without_fabricating_tool_calls(state,source,unknown):
    t=turn(state)
    result=tool_profile(t,provenance_version='source-provenance-v3')
    assert result['top_level_calls']==result['total_tool_operations']==0
    assert result['source_fallback_attempted'] is source
    assert result['unclassified_operations']==unknown
    assert result['prefetch_provenance']==t.prefetch_provenance


def test_captured_context_change_cannot_reuse_provenance_hash():
    t=turn('source');t.prefetch_context='changed'
    with pytest.raises(ValueError,match='actual context'):
        tool_profile(t,provenance_version='source-provenance-v3')


def test_no_injection_has_no_source_penalty_and_legacy_protocols_keep_semantics():
    t=turn('source')
    assert tool_profile(t,provenance_version='source-provenance-v2')['source_fallback_attempted'] is False
    assert observe(t.prefetch_provenance,'') is None


def test_invalid_native_condition_is_rejected():
    with pytest.raises(ValueError):
        observe({'protocol':VERSION,'provider':'unknown','state':'retained','reason':'unproven'},'text')


def test_supermemory_v5_hybrid_context_cannot_imply_retention():
    t=turn('unverified')
    t.prefetch_provenance=observe({'protocol':VERSION,'provider':'supermemory','state':'unverified',
        'reason':'Native hybrid formatter omits original result type.'},t.prefetch_context)
    result=tool_profile(t,provenance_version='source-provenance-v3')
    assert result['unclassified_operations']==1
    from pacbench.followups import tool_category
    assert tool_category('supermemory-read-document',{})=='source_evidence'
    assert tool_category('supermemory-search',{'query':'Friday'})=='unclassified'
