"""Synthetic full Week through the real offline batch CLI and both native session layouts."""
from dataclasses import asdict
import hashlib
import json
import subprocess
import sys

import pytest

from pacbench.judging.paired import BASELINE, PROTOCOL, fingerprint
from pacbench.semantic_grade import judge_prompt
from pacbench.workflows.runner import judge_options
from pacbench.campaigns.pizza_week import compile_week


def synthetic_export(layout):
    turns = compile_week('continuing-account')[2]
    sessions, rows = {}, []
    for turn in turns:
        sid = 'one-hermes' if layout == 'single' else 'native-' + turn.session
        history, times = sessions.setdefault(sid, ([], []))
        history.append(dict(role='user', content=turn.prompt))
        if turn.id == 'game-reference':
            history.extend([dict(role='assistant', content='I will check the game.',
                                 tool_calls=[{'id':'tool-1'}]),
                            dict(role='tool', content='Tool-only text.', tool_call_id='tool-1')])
        answer = 'Synthetic saved answer for ' + turn.id + '.'
        history.append(dict(role='assistant', content=answer))
        times.append(turn.at)
        rows.append(dict(turn=asdict(turn), native_session=sid, answer=answer,
                         conversation=list(history), conversation_times=list(times),
                         tool_results=['Private diagnostic sentinel.'], criteria=None))
    return turns, rows


def save_rows(path, rows):
    body = ''.join(json.dumps(row) + '\n' for row in rows).encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def cli(command, path, digest, layout, target, *args):
    return subprocess.run([sys.executable, '-m', 'pacbench.question_judge_batch', command, str(path),
        '--sha256', digest, '--session-layout', layout, '--output', str(target), *map(str, args)],
        capture_output=True, text=True, check=False)


@pytest.mark.parametrize('layout', ['single', 'logical'])
def test_paired_full_executable(tmp_path, layout):
    turns, rows = synthetic_export(layout)
    source, plan_path = tmp_path/'private.jsonl', tmp_path/'plan.json'
    sha = save_rows(source, rows)
    proc = cli('plan', source, sha, layout, plan_path)
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(plan_path.read_text())
    assert (plan['manifest']['turns'], plan['manifest']['questions'],
            plan['manifest']['criteria_per_arm'], len(plan['cells'])) == (49,47,192,94)
    assert plan['manifest']['cell_order'] == [dict(turn=c['turn'],arm=c['arm'],request_sha256=fingerprint(c['request']))
                                              for c in plan['cells']]
    assert plan['plan_sha256'] == fingerprint(plan['manifest'])
    assert plan_path.stat().st_mode & 0o077 == 0
    assert 'Private diagnostic sentinel.' not in plan_path.read_text()
    assert 'Tool-only text.' not in plan_path.read_text()
    assert 'Synthetic saved answer' not in proc.stdout

    results = {}
    question_number = 0
    for turn, row in zip(turns, rows, strict=True):
        if not turn.claims:
            continue
        cells = [cell for cell in plan['cells'] if cell['turn'] == turn.id]
        assert [cell['arm'] for cell in cells] == ([BASELINE,PROTOCOL] if question_number % 2 == 0 else [PROTOCOL,BASELINE])
        question_number += 1
        cells.sort(key=lambda c: c['arm'] != BASELINE)
        surface = dict(history=row['conversation'], conversation_times=row['conversation_times'])
        assert cells[0]['request']['messages'] == [dict(role='user', content=judge_prompt(
            turn.prompt, row['answer'], turn.claims, **judge_options(turn, surface)))]
        payload = json.loads(cells[1]['request']['messages'][1]['content'])
        assert payload['prior_public_dialogue'] == judge_options(turn,surface)['conversation']
        if turn.id == 'friday-lunch-time':
            assert any(r['content'] == 'I will check the game.' for r in payload['prior_public_dialogue'])
        if any(claim.evidence[0].startswith('turn/') for claim in turn.claims):
            correction = turn.claims[0].evidence[0]
            bound = next(p for p in plan['manifest']['pairs'] if p['turn'] == turn.id)
            assert any(v['source_id'] == correction for v in bound['anchor_bindings'].values())
        results[turn.id] = {}
        for cell in cells:
            verdicts = [dict(id=c.id, verdict='missed', reason='synthetic omission') if cell['arm']==BASELINE
                        else dict(id=c.id, relation='absent', answer_quote=None, reason='synthetic omission')
                        for c in turn.claims]
            results[turn.id][cell['arm']] = dict(request_sha256=fingerprint(cell['request']),
                                           finish_reason='stop',text=json.dumps({'verdicts':verdicts}))
    replies = tmp_path/'replies.json'
    replies.write_text(json.dumps({'plan_sha256': plan['plan_sha256'], 'responses':results}))
    report = tmp_path/'report.json'
    proc = cli('interpret', source, sha, layout, report, '--replies', replies)
    assert proc.returncode == 0, proc.stderr
    marks = json.loads(report.read_text())
    assert sum(x['graded'] for x in marks['arms'].values()) == 384
    assert len(marks['judgments']) == 94 and marks['receipt_status'] == 'offline-unverified'
    assert sha == hashlib.sha256(source.read_bytes()).hexdigest()
    existing = report.read_bytes()
    assert cli('interpret', source, sha, layout, report, '--replies', replies).returncode == 2
    assert report.read_bytes() == existing
    # Missing judge replies retain planned coverage; a response attached to the wrong request is refused.
    first = next(iter(results.values()))
    first.pop(BASELINE)
    replies.write_text(json.dumps({'plan_sha256':plan['plan_sha256'], 'responses':results}))
    partial = tmp_path/'partial.json'
    assert cli('interpret',source,sha,layout,partial,'--replies',replies).returncode == 0
    assert json.loads(partial.read_text())['judgments'][0]['state'] == 'missing_judge_response'
    first[PROTOCOL]['request_sha256'] = '0'*64
    replies.write_text(json.dumps({'plan_sha256':plan['plan_sha256'], 'responses':results}))
    bad = tmp_path/'bad-report'
    assert cli('interpret',source,sha,layout,bad,'--replies',replies).returncode == 2
    assert not bad.exists()


@pytest.mark.parametrize('fault', ['missing-correction','changed-correction','foreign-prefix',
    'rotated-session','reused-session','layout','stale-pin','future','wrong-final','duplicate-turn'])
def test_refuses_bad_native_export(tmp_path, fault):
    layout = 'logical'
    _, rows = synthetic_export(layout)
    by_id = {r['turn']['id']:r for r in rows}
    if fault == 'missing-correction':
        rows.remove(by_id['weekend-correction'])
    elif fault == 'changed-correction':
        by_id['ben-correction']['conversation'][-2] = {'role':'user','content':'Fabricated correction'}
    elif fault == 'foreign-prefix':
        by_id['friday-lunch-time']['conversation'][1] = {'role':'assistant','content':'Altered commentary'}
    elif fault == 'rotated-session':
        by_id['friday-lunch-time']['native_session'] = 'replacement-session'
    elif fault == 'reused-session':
        rows[1]['native_session'] = rows[0]['native_session']
    elif fault == 'layout':
        layout = 'single'
    elif fault == 'future':
        rows[0]['conversation_times'] = [rows[-1]['turn']['at']]
    elif fault == 'wrong-final':
        rows[-1]['answer'] = 'Other answer.'
    elif fault == 'duplicate-turn':
        rows.append(rows[-1])
    path, out = tmp_path/'private.jsonl', tmp_path/'out'
    sha = save_rows(path,rows)
    proc = cli('plan', path, '0'*64 if fault=='stale-pin' else sha, layout, out)
    assert proc.returncode == 2, proc.stdout
    assert not out.exists()


def test_verified_reader_projection_and_no_implied_legacy_binding(tmp_path):
    from pacbench.judging.week import Week

    _, rows = synthetic_export('logical')
    week = Week()
    from pacbench.workflows.stream import fingerprint as schedule_fingerprint
    docs = [dict(filename=d.filename, sha256=d.sha256, cutoff=d.cutoff, source_ids=list(d.source_ids),
                 bodies=list(d.bodies)) for d in week.documents]
    binding = dict(document_schedule_sha256=schedule_fingerprint(
        [{k: d[k] for k in ('filename','cutoff','sha256','source_ids')} for d in docs]))
    source = tmp_path/'export.jsonl'
    def run(name, documents):
        origin = dict(projected_sources_sha256=fingerprint(documents), session_layout='logical', binding=binding,
                      cohort_manifest_sha256='a'*64,entry_manifest_sha256='b'*64)
        for row in rows:
            row['retained_origin'] = origin
        rows[0]['retained_documents'] = documents
        out = tmp_path/name
        proc = cli('plan',source,save_rows(source,rows),'logical',out)
        return proc, out
    proc, out = run('verified', docs)
    assert proc.returncode == 0, proc.stderr
    audit = json.loads(out.read_text())['manifest']['projection_audit']
    assert audit['projected_records_sha256'] == week.audit_projection(week.documents)['projected_records_sha256']
    assert (audit['source_records'], audit['evidence_uses'], audit['correction_uses']) == (142,383,4)
    shifted = [dict(d, cutoff='2026-10-06T21:01:00-04:00') if i == 0 else d for i,d in enumerate(docs)]
    proc, out = run('shifted', shifted)
    assert proc.returncode == 2 and 'release clock' in proc.stderr and not out.exists()
    # The first source is used by draft-before. A digest of the altered source export cannot validate its anchors.
    first = week.by_id['draft-before'].claims[0].evidence[0]
    target = next(d for d in docs if first in d['source_ids'])
    target['bodies'][target['source_ids'].index(first)] = 'No source passage.'
    proc, out = run('tampered', docs)
    assert proc.returncode == 2 and not out.exists()
    proc, out = run('legacy', [dict(d,bodies=[]) for d in docs])
    assert proc.returncode == 2 and 'record body bindings' in proc.stderr and not out.exists()


def test_reviewed_native_bridge_on_sealed_synthetic_checkpoint(tmp_path):
    """Exercise the native reader and paired planner from the integrated checkout."""
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    reader = Path(os.environ.get('PACBENCH_RETAINED_READER', root)).resolve()
    cohort = tmp_path/'cohort.json'
    make = """from pathlib import Path
import sys
from tests.test_cohort_regrade import CohortRegradeTests
fixture = CohortRegradeTests()
fixture.root = Path(sys.argv[1])
fixture.cohort([fixture.make_run('cognee',count=49,seal=True)])
"""
    subprocess.run([sys.executable,'-c',make,str(tmp_path)],cwd=reader,check=True)
    module = root/'pacbench/question_judge_batch.py'
    export = tmp_path/'native.private.jsonl'
    manifest_sha = hashlib.sha256(cohort.read_bytes()).hexdigest()

    def native_export(destination):
        return subprocess.run([sys.executable, str(module), 'export-retained', str(cohort),
                               '--arm', 'cognee', '--sha256', manifest_sha, '--output', str(destination)],
                              cwd=reader, env=dict(os.environ, PYTHONPATH=str(reader)), text=True, capture_output=True)

    proc = native_export(export)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    plan = tmp_path/'verified.plan'
    proc = cli('plan',export,summary['export_sha256'],summary['session_layout'],plan)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(plan.read_text())
    assert len(result['cells']) == 94
    assert result['manifest']['projection_audit']['state'] == 'verified'
    assert result['manifest']['retained_origin']['arm'] == 'cognee'
    assert result['manifest']['retained_origin']['cohort_manifest_sha256'] == hashlib.sha256(cohort.read_bytes()).hexdigest()
    assert result['manifest']['retained_origin']['entry_manifest_sha256'] == json.loads(cohort.read_text())['runs'][0]['sha256']

    from pacbench.campaigns.pizza_week import compile_week
    from pacbench.workflows.spec import instant

    journal = tmp_path/'cognee/workflow/workflow.jsonl'
    first_turn = compile_week('continuing-account')[2][0]
    events = []
    for line in journal.read_text().splitlines():
        event = json.loads(line)
        if event['phase'] == 'submit' and instant(event['at']) > instant(first_turn.at):
            break
        events.append(event)
    first_readback = next(event for event in events if event['phase'] == 'readback')
    first_readback['evidence']['at'] = '2099-10-06T21:01:00-04:00'
    events.append(dict(phase='dispatch', question=first_turn.id, prompt=first_turn.prompt, at=first_turn.at))
    journal.write_text(''.join(json.dumps(event) + '\n' for event in events))
    late = tmp_path/'late.private.jsonl'
    proc = native_export(late)
    assert proc.returncode == 2 and not late.exists()
    assert 'is late at question' in proc.stderr or 'differs at its original cutoff' in proc.stderr

    journal.unlink()
    missing = tmp_path/'missing.private.jsonl'
    proc = native_export(missing)
    assert proc.returncode == 2 and 'lacks original native ingestion evidence' in proc.stderr and not missing.exists()


def test_export_refuses_an_unreviewed_reader_before_opening_input(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pacbench import cohort_regrade
    from pacbench.judging import batch as question_judge_batch

    changed = tmp_path/'changed_reader.py'
    changed.write_text('# unreviewed reader\n')
    monkeypatch.setattr(cohort_regrade.retained, '__file__', str(changed))
    with pytest.raises(ValueError, match='retained reader differs'):
        question_judge_batch.export_retained(SimpleNamespace())
