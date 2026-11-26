from pathlib import Path
import subprocess
import pytest
from pacbench.reviewed_runtime import audit_only_compatibility,execution_digest,REVIEWED_SHA,RUNTIME_AST_SHA


def test_unreviewed_source_and_runtime_changes_are_refused(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'pacbench/followups.py'
    target = tmp_path / 'pacbench' / 'followups.py'
    target.parent.mkdir()
    current = source.read_text()
    target.write_text(current + '\n# unreviewed audit-only revision\n')
    with pytest.raises(ValueError):
        audit_only_compatibility(tmp_path, target, REVIEWED_SHA)
    changed_runtime = current + '\nUNREVIEWED_RUNTIME_CHANGE = True\n'
    assert execution_digest(changed_runtime) != execution_digest(current)
    target.write_text(changed_runtime)
    with pytest.raises(ValueError):
        audit_only_compatibility(tmp_path, target, REVIEWED_SHA)


def test_exact_reviewed_pair_preserves_runtime_and_frozen_audit(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    revision = 'a7bc36729db23152f61b928df5cda2149e717859'
    git = ['git', '-C', str(repo)]
    if subprocess.run([*git, 'cat-file', '-e', f'{revision}^{{commit}}'], capture_output=True).returncode:
        pytest.skip('Fetch the historical Pizza Week archive in docs/pizza-week-continuation.md')
    source = subprocess.check_output([*git, 'show', f'{revision}:pacbench/followups.py'])
    target=tmp_path/'pacbench/followups.py';target.parent.mkdir();target.write_bytes(source)
    witness=audit_only_compatibility(tmp_path,target,REVIEWED_SHA)
    assert witness['execution_ast_sha256']==RUNTIME_AST_SHA and witness['frozen_review_rewritten'] is False
    original=target.read_text()
    # Even another audit-only edit needs an explicit new reviewed pair.
    target.write_text(original+'\n# another edit\n')
    with pytest.raises(ValueError):audit_only_compatibility(tmp_path,target,REVIEWED_SHA)
    target.write_text(original.replace('minimum_initial_correct', 'changed_runtime_policy'))
    assert execution_digest(target.read_text())!=RUNTIME_AST_SHA
    with pytest.raises(ValueError):audit_only_compatibility(tmp_path,target,REVIEWED_SHA)
    target.write_text(original)
    with pytest.raises(ValueError):audit_only_compatibility(tmp_path,target,'unreviewed')
    other=target.with_name('different.py');other.write_text(original)
    with pytest.raises(ValueError):audit_only_compatibility(tmp_path,other,REVIEWED_SHA)
