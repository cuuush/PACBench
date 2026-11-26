import pytest
from pacbench.provider_versions import memcal_pin, MEMCAL_REVISION, MEMCAL_REVIEW_REVISION


def test_review_sha_is_explicit_and_legacy_default_is_preserved(monkeypatch):
    monkeypatch.delenv('PACBENCH_MEMCAL_CONDITION',raising=False)
    assert memcal_pin()==('stable-0.8.11',MEMCAL_REVISION,'0.8.11')
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION','wiki-supersession-review-da58057-v1')
    assert memcal_pin()==('wiki-supersession-review-da58057-v1',MEMCAL_REVIEW_REVISION,'0.8.12')
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION','0.8.12')
    with pytest.raises(RuntimeError,match='Unknown'):memcal_pin()


@pytest.mark.parametrize('actual,dirty,passes',[
    (MEMCAL_REVIEW_REVISION,False,True),(MEMCAL_REVISION,False,False),
    ('f'*40,False,False),(MEMCAL_REVIEW_REVISION,True,False)])
@pytest.mark.parametrize('condition,expected',[
    ('wiki-supersession-review-da58057-v1',MEMCAL_REVIEW_REVISION),
    ('wiki-preference-operations-review-c19d216-v1','c19d216a7e5dd3913f69ab79c24d2d339bacef16')])
def test_review_verification_requires_exact_clean_git_tree(tmp_path,monkeypatch,actual,dirty,passes,condition,expected):
    from pacbench.provider_versions import verify_memcal_source
    plugin=tmp_path/'integrations/hermes/memcal/__init__.py';plugin.parent.mkdir(parents=True);plugin.touch()
    monkeypatch.setenv('PACBENCH_MEMCAL_SRC',str(tmp_path))
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION',condition)
    if actual==MEMCAL_REVIEW_REVISION:actual=expected
    def git(cmd,**kwargs):
        if cmd[-1]=='--show-toplevel':return str(tmp_path)+'\n'
        if cmd[-1]=='HEAD':return actual+'\n'
        if cmd[-1]=='--porcelain':return ' M changed.py\n' if dirty else ''
        raise AssertionError(cmd)
    monkeypatch.setattr('pacbench.provider_versions.subprocess.check_output',git)
    if passes:assert verify_memcal_source(tmp_path)['revision']==expected
    else:
        with pytest.raises(RuntimeError):verify_memcal_source(tmp_path)


def test_context_repair_is_a_separate_exact_review_condition(monkeypatch):
    from pacbench.provider_versions import MEMCAL_CONTEXT_CONDITION,MEMCAL_CONTEXT_REVISION
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION',MEMCAL_CONTEXT_CONDITION)
    assert memcal_pin()==(MEMCAL_CONTEXT_CONDITION,MEMCAL_CONTEXT_REVISION,'0.8.12')
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION','0.8.12')
    with pytest.raises(RuntimeError,match='Unknown'):memcal_pin()


def test_checkout_uses_old_memcal_location_only_for_selected_revision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pacbench.provider_versions import memcal_source

    monkeypatch.delenv('PACBENCH_MEMCAL_SRC', raising=False)
    monkeypatch.delenv('PACBENCH_MEMCAL_CONDITION', raising=False)
    repo = tmp_path / 'checkout'
    legacy = tmp_path / 'memcal-stable-20261113'
    (legacy / '.git').mkdir(parents=True)
    monkeypatch.setattr('pacbench.provider_versions.subprocess.run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=MEMCAL_REVIEW_REVISION))
    assert memcal_source(repo) == repo / 'vendor/memcal'
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION', 'wiki-supersession-review-da58057-v1')
    assert memcal_source(repo) == legacy
