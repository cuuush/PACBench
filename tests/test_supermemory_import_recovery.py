import pytest
from pacbench.providers.supermemory.import_recovery import qualify


def test_accepted_recovery_requires_exact_source_and_both_native_phases():
    raw={'id':'day','content':'frozen source','metadata':{'source':'daily-transcript'}}
    status={'id':'day','status':'done','dreaming_status':'done'}
    kwargs=dict(identifier='day',text='frozen source',metadata=raw['metadata'])
    qualify(raw,status,**kwargs)
    for bad in [{**status,'dreaming_status':'processing'},{**status,'status':'failed'}]:
        with pytest.raises(ValueError):qualify(raw,bad,**kwargs)
    with pytest.raises(ValueError):qualify({**raw,'content':'truncated'},status,**kwargs)
    qualify({**raw,'content':'frozen source','metadata':{**raw['metadata'],'temporalContext':'native-generated'}},status,
            **{**kwargs,'text':'frozen source\n'})
    with pytest.raises(ValueError):qualify({**raw,'content':'frozen source '},status,**kwargs)

def test_pending_timeout_requires_an_identified_accepted_native_history():
    from pacbench.providers.supermemory.import_recovery import accepted_failure
    row={'state':'failed','ready':False,'result':{'reason':'Native memory processing deadline exceeded',
        'result':{'id':'day','status':'queued'},'processing_status':[{'id':'day','status':'queued'}]}}
    assert accepted_failure(row)=='day'
    import copy
    for change in ['no_history','wrong_id','rejected','no_id','already_ready','other_reason']:
        d=copy.deepcopy(row)
        if change=='no_history':d['result']['processing_status']=[]
        if change=='wrong_id':d['result']['processing_status'][0]['id']='other'
        if change=='rejected':d['result']['processing_status'][0]['status']='failed'
        if change=='no_id':d['result']['result'].pop('id')
        if change=='already_ready':d['ready']=True
        if change=='other_reason':d['result']['reason']='Unknown failure'
        with pytest.raises(ValueError):accepted_failure(d)
