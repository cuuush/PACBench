"""Two coordinators cannot admit native work into the same output directory."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest


def test_native_output_directory_claim_is_atomic_before_provider_or_model_work(tmp_path,monkeypatch):
    from pacbench import native_pilot,context_isolation
    barrier=Barrier(2)
    monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter')
    monkeypatch.delenv('PACBENCH_ROLE_ROUTES',raising=False)
    monkeypatch.setattr(native_pilot,'provider_names',lambda:['mem0'])
    monkeypatch.setattr(native_pilot,'require_live_cost_boundary',lambda:{})
    monkeypatch.setattr(context_isolation,'validate_recall_environment',lambda:barrier.wait(timeout=5))
    class Claimed(Exception):pass
    def before_native_work(*args,**kwargs):raise Claimed('Only winner reaches first summary, before prepared import or provider setup')
    monkeypatch.setattr(native_pilot,'save',before_native_work)
    def attempt():
        try:native_pilot.run(tmp_path/'unread-source',tmp_path/'same-output',providers=['mem0'],inference_route='openrouter')
        except (Claimed,FileExistsError) as error:return type(error)
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(lambda _:attempt(),range(2)))
    assert outcomes.count(Claimed)==1 and outcomes.count(FileExistsError)==1
