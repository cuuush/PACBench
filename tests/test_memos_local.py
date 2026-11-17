from pathlib import Path

from pacbench.native_pilot import native_provider_name, provider_names
from pacbench.followups import tool_category
from provider_adapters.memos_local import plan_config


def test_memos_uses_native_provider_name_without_conflating_results():
    assert native_provider_name('memos')=='memtensor'
    assert native_provider_name('mem0')=='mem0'
    assert 'memos' in provider_names()


def test_memos_local_memory_keeps_selected_model_and_disables_other_host_inference():
    result=plan_config(Path('/offline/memos'),'selected-model','https://openrouter.ai/api/v1','inert')
    assert result['llm']['fallbackToHost'] is False
    assert result['llm']['model']=='selected-model'
    assert result['embedding']['local']['offline'] is True
    assert result['embedding']['provider']=='local'
    assert result['hub']['enabled'] is result['telemetry']['enabled'] is False


def test_memos_original_trace_search_is_source_access():
    assert tool_category('memos_search',{'query':'plan'})=='source_evidence'
    assert tool_category('memos_get',{'kind':'trace'})=='source_evidence'
    assert tool_category('memos_get',{'kind':'world_model'})=='memory_detail'
    assert tool_category('memos_timeline',{})=='source_messages'


def test_memos_native_none_condition_is_explicit_and_preserves_other_settings(monkeypatch):
    before=plan_config(Path('/offline'),'openai/gpt-6-luna','url','inert')
    monkeypatch.setenv('PACBENCH_MEMOS_REASONING_CONDITION','native-none-v1')
    after=plan_config(Path('/offline'),'openai/gpt-6-luna','url','inert')
    assert after['llm'].pop('reasoning')=={'effort':'none'}
    assert after==before


def test_native_stop_reaps_run_owned_shared_bridge_without_touching_store(tmp_path):
    import os,subprocess,sys
    from types import SimpleNamespace
    from provider_adapters.memos_local import stop_native
    root=tmp_path/'arm';root.mkdir();store=root/'kept.db';store.write_bytes(b'original-store')
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],cwd=root,
        env={k:os.environ[k] for k in ('PATH','LD_LIBRARY_PATH') if k in os.environ},start_new_session=True)
    try:
        proof=stop_native(root,SimpleNamespace(poll=lambda:0))
        child.wait(timeout=3)
        assert proof['state']=='verified-inventory-stopped' and not proof['remaining_processes']
        assert any(p['pid']==child.pid for p in proof['observed_processes'])
        assert store.read_bytes()==b'original-store' and proof['stores_deleted'] is False and proof['ledger_modified'] is False
    finally:
        if child.poll() is None:child.kill();child.wait()
