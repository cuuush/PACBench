import json
from pathlib import Path

from pacbench.explorer_data import reviewed_overlay


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def test_selected_overlay_requires_valid_audit_and_confined_source(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    overlay = run / 'reviews' / 'date-scope-v2'
    save(run / 'reviews' / 'selected.json', {'path':'date-scope-v2', 'judge_protocol':'v2'})
    save(overlay / 'manifest.json', {'source_root':'../..', 'judge_protocol':'v2', 'cases':[
        {'judgment':'repeat-1/memcal/turns/check/judgment.json'}]})
    save(overlay / 'cases/memcal/turns/check/result.json', {'grade':{'slots':[{'id':'x','verdict':'correct'}]}})
    import pacbench.rejudge
    monkeypatch.setattr(pacbench.rejudge, 'validate_published_audit', lambda _: 1)
    reviewed = reviewed_overlay(run)
    assert reviewed['state'] == 'valid'
    assert reviewed['cases']['repeat-1/memcal/turns/check/judgment.json']['grade']['claims'][0]['id'] == 'x'
    def invalid(_):
        raise ValueError('audit failed')
    monkeypatch.setattr(pacbench.rejudge, 'validate_published_audit', invalid)
    assert reviewed_overlay(run)['state'] == 'invalid'
    save(run / 'reviews' / 'selected.json', {'path':'../outside', 'judge_protocol':'v2'})
    assert reviewed_overlay(run)['state'] == 'invalid'
