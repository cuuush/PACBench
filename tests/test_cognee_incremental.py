"""Incremental native extraction must not inherit a prior item's completion."""
from types import SimpleNamespace
import hashlib
import pytest
from provider_adapters.cognee_local import qualify_incremental_document,QualificationError

class Backend:
    url='http://127.0.0.1:8011'
    def __init__(self):self.docs={'old':'original'};self.writes=0
    def ensure_dataset(self,**kw):pass
    def list_datasets(self,**kw):return [{'id':'dataset','name':'pacbench_test'}]
    def list_dataset_data(self,**kw):return [{'id':i} for i in self.docs] if kw['offset']==0 else []
    def read_raw_data(self,**kw):return self.docs[kw['data_id']]
    def remember_permanent(self,**kw):
        self.writes+=1;self.docs['new']=kw['text']
        assert kw['session_ids']==[]
        return SimpleNamespace(raw={'status':'running'})
    def dataset_pipeline_status(self,**kw):return 'DATASET_PROCESSING_COMPLETED'


def status(completed):
    items=[{'id':'old','completed':True},{'id':'new','completed':completed}]
    return {'items':items,'total':2,'completed':1+int(completed),'pending':int(not completed)}


def qualify(b,read,**kwargs):
    return qualify_incremental_document(b,service_url=b.url,dataset='pacbench_test',text='next day raw thread',
        prior=[{'data_id':'old','source_sha256':hashlib.sha256(b'original').hexdigest()}],
        processing_status=read,sleep=lambda _:None,**kwargs)


def test_old_completed_run_does_not_pass_until_new_item_is_completed():
    b=Backend();observed=[]
    def read(**kw):
        observed.append(kw);return status(len(observed)>1)
    result=qualify(b,read)
    assert b.writes==1 and len(observed)==2
    assert result['item_completion_observations']==[status(False),status(True)]
    assert result['data_id']=='new' and result['processing_complete']


def test_pending_new_item_times_out_without_a_second_write():
    b=Backend();t=[0]
    def clock():t[0]+=.1;return t[0]
    with pytest.raises(QualificationError,match='deadline'):
        qualify(b,lambda **kw:status(False),clock=clock,timeout=3)
    assert b.writes==1


def test_changed_old_source_refuses_new_submission():
    b=Backend();b.docs['old']='changed'
    with pytest.raises(QualificationError,match='source changed'):qualify(b,lambda **kw:status(True))
    assert b.writes==0


def test_same_count_with_wrong_completed_identity_does_not_pass():
    b=Backend();t=[0]
    def clock():t[0]+=.1;return t[0]
    wrong=status(True);wrong['items'][1]['id']='unrelated'
    with pytest.raises(QualificationError,match='deadline'):
        qualify(b,lambda **kw:wrong,clock=clock,timeout=3)
    assert b.writes==1


def test_invalid_native_counts_fail_closed():
    b=Backend();wrong=status(True);wrong['completed']=1
    with pytest.raises(QualificationError,match='completion response'):
        qualify(b,lambda **kw:wrong)
    assert b.writes==1
