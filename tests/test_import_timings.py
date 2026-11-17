import pytest
from pacbench.import_timings import union_seconds

def test_parallel_api_spans_are_not_double_counted():
    assert union_seconds([(10,20),(0,15),(14,16),(30,31)])==21
    assert union_seconds([])==0
    with pytest.raises(ValueError):union_seconds([(2,1)])
