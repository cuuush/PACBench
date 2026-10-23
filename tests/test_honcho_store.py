"""A diagnostic restart must prove the original source survived unchanged."""
from copy import deepcopy
import pytest
from provider_adapters.honcho_local import verify_persisted_messages


def message():
    return {'id':'native-1','session_id':'source-thread','peer_id':'Riley',
        'content':'See you Saturday.','created_at':'2026-10-19T09:00:00-04:00',
        'metadata':{'source_id':'message-1','thread':'dm'}}


def test_native_ids_and_source_survive_timezone_equivalent_database_export():
    sent=message();actual=deepcopy(sent);actual['created_at']='2026-10-19T13:00:00+00:00'
    assert verify_persisted_messages([{'messages':[sent]}],[actual])==['native-1']


@pytest.mark.parametrize('change',['id','session_id','peer_id','content','created_at','metadata','missing','duplicate'])
def test_reopen_rejects_missing_replaced_or_changed_native_sources(change):
    sent=message();actual=deepcopy(sent)
    if change not in {'missing','duplicate'}:
        actual[change]={'source_id':'other'} if change=='metadata' else 'changed'
    rows=[] if change=='missing' else [actual,actual] if change=='duplicate' else [actual]
    with pytest.raises(ValueError):verify_persisted_messages([{'messages':[sent]}],rows)
