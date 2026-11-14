import pytest
from pacbench.supermemory_import_recovery import qualify


def test_accepted_recovery_requires_exact_source_and_both_native_phases():
    raw={'id':'day','content':'frozen source','metadata':{'source':'daily-transcript'}}
    status={'id':'day','status':'done','dreaming_status':'done'}
    kwargs=dict(identifier='day',text='frozen source',metadata=raw['metadata'])
    qualify(raw,status,**kwargs)
    for bad in [{**status,'dreaming_status':'processing'},{**status,'status':'failed'}]:
        with pytest.raises(ValueError):qualify(raw,bad,**kwargs)
    with pytest.raises(ValueError):qualify({**raw,'content':'truncated'},status,**kwargs)
