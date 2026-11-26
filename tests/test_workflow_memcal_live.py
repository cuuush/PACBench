from types import SimpleNamespace

import pytest

from pacbench.providers.memcal import workflow as launcher
from pacbench.providers.memcal.contract import verify_contract
from pacbench.campaigns.pizza_week import compile_week


def test_launch_requires_exact_original_binding():
    _, docs, turns, binding = compile_week('continuing-account')
    with pytest.raises(ValueError, match='Frozen MemCal'):
        verify_contract(docs, turns, {**binding, 'sha256':'0'*64})


def test_prepared_store_is_claimed_once_and_old_outputs_are_preserved(tmp_path):
    campaign = tmp_path/'canary'
    store = campaign/'memcal/memcal-home'
    store.mkdir(parents=True)
    witness = store/'native'
    witness.write_bytes(b'pre-existing native data')
    site = SimpleNamespace(memcal_home=str(store))
    assert launcher.claim_prepared_arm(campaign, site) == store.parent
    with pytest.raises(ValueError, match='fresh campaign'):
        launcher.claim_prepared_arm(campaign, site)
    assert witness.read_bytes() == b'pre-existing native data'


def test_launch_blocked_before_credit_or_model_access(tmp_path, monkeypatch):
    from pacbench.providers.memcal.site import MemCalSite, MemCalSiteError
    site = MemCalSite('dedicated', str(tmp_path/'absent'), str(tmp_path/'absent-store'),
                      'chatdb', '127.0.0.1', 1993, 'synthetic@example.invalid')
    monkeypatch.setattr(launcher, '_run', lambda *a: pytest.fail('opened a paid or account path'))
    with pytest.raises(MemCalSiteError):
        launcher.run_live(tmp_path/'run', site, None, tmp_path/'absent-settings', canary=True)
    assert not (tmp_path/'run').exists()


def test_request_source_verifier_uses_registered_connection(tmp_path):
    from pacbench.workflows.source_plan import SourcePlan
    _, docs, turns, _ = compile_week('continuing-account')
    with SourcePlan(tmp_path, docs, turns) as plan:
        with SourcePlan.from_connection(plan.db, plan.digest) as view:
            assert next(view.iter_turns()) == next(plan.iter_turns())
        with pytest.raises(ValueError, match='differs from the frozen scope'):
            SourcePlan.from_connection(plan.db, 'other')
        assert plan.turn_count == 49


def test_appended_wire_trace_rejects_replacement_and_keeps_only_current_turn(tmp_path):
    from pacbench.workflows.native import appended_rows, trace_position
    path = tmp_path/'wire.jsonl'
    path.write_bytes(b'{"previous":true}\n')
    cursor = trace_position(path)
    with path.open('ab') as stream:
        stream.write(b'{"current":true}\n')
    assert list(appended_rows(path, cursor)) == [{'current':True}]
    path.write_bytes(b'{"previous":null}\n{"current":true}\n')
    with pytest.raises(ValueError, match='boundary changed'):
        list(appended_rows(path, cursor))
