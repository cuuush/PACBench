"""Offline transfer of original accounting, never a blank replacement ledger."""
import hashlib,json
from pathlib import Path
import pytest
from pacbench.accounting_transfer import export_state,restore_state
from pacbench.direct_accounting import DirectBudget,MODEL

THREAD='6af72854-8874-8191-aa35-2ccdda0ab002'

@pytest.fixture
def state(tmp_path):
    root=tmp_path/'source';root.mkdir()
    (root/'authorization.json').write_text(json.dumps({'approved_total_usd':'20'}))
    (root/'ledger.json').write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':100,
        'prompt_per_million':'1','completion_per_million':'2','max_completion_tokens':50,
        'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    b=DirectBudget(root/'ledger.json');r=b.reserve('original',{'model':MODEL});b.settle(r['id'],200,b'{}')
    b.reserve('historical-pending',{'model':MODEL})
    return root,b


def test_transfer_freezes_source_preserves_every_hold_and_restores_original_bytes(state,tmp_path):
    root,b=state;before=b.snapshot();archive=tmp_path/'state.tar.gz'
    result=export_state(archive,THREAD,state_dir=root)
    assert result['call_count']==2 and b.snapshot()==before
    with pytest.raises(RuntimeError,match='frozen for session transfer'):b.reserve('other',{'model':MODEL})
    dest=tmp_path/'restored';out=restore_state(archive,result['sha256'],THREAD,destination=dest)
    assert out['state']=='restored-frozen'
    assert (dest/'ledger.json').read_bytes()==(root/'ledger.json').read_bytes()
    assert DirectBudget(dest/'ledger.json').snapshot()==before


def test_activation_changes_only_handoff_metadata_and_source_remains_frozen(state,tmp_path):
    root,b=state;archive=tmp_path/'state.tar.gz';result=export_state(archive,THREAD,state_dir=root)
    dest=tmp_path/'active';restore_state(archive,result['sha256'],THREAD,destination=dest,activate=True)
    source=json.loads((root/'ledger.json').read_text());active=json.loads((dest/'ledger.json').read_text())
    assert source['calls']==active['calls'] and source['config']==active['config']
    assert source['handoff']['frozen'] is True and active['handoff']['frozen'] is False
    assert active['handoff']['active_thread']==THREAD


@pytest.mark.parametrize('fault',['hash','thread','existing'])
def test_restore_rejects_wrong_identity_or_overwrite(state,tmp_path,fault):
    root,b=state;archive=tmp_path/'state.tar.gz';result=export_state(archive,THREAD,state_dir=root)
    dest=tmp_path/'restored';expected=result['sha256'];thread=THREAD
    if fault=='hash':expected='0'*64
    elif fault=='thread':thread='00000000-0000-0000-0000-000000000000'
    else:dest.mkdir();(dest/'ledger.json').write_text('existing history')
    with pytest.raises(ValueError):restore_state(archive,expected,thread,destination=dest)
    if fault=='existing':assert (dest/'ledger.json').read_text()=='existing history'


def test_export_refuses_embedded_runtime_credential_and_keeps_source_frozen(state,tmp_path,monkeypatch):
    root,b=state;secret='offline-credential-never-export'
    monkeypatch.setenv('OPENROUTER_API_KEY',secret)
    (root/'authorization.json').write_text(json.dumps({'approved_total_usd':'20','unexpected':secret}))
    with pytest.raises(ValueError,match='Credential'):export_state(tmp_path/'refused.tar.gz',THREAD,state_dir=root)
    assert json.loads((root/'ledger.json').read_text())['handoff']['frozen'] is True
    assert not (tmp_path/'refused.tar.gz').exists()
