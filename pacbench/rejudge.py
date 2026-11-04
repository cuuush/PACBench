"""Offline-frozen, separately receipted rejudgment of saved provider answers."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from .evidence_store import read_json
from .model_calls import ModelClient
from .stop import OperatorStop, check_stop
from .semantic_grade import (Claim, BenchmarkContractError, DATE_SCOPED_JUDGE_PROTOCOL,
    LEGACY_JUDGE_PROTOCOL, RETRY_GUIDANCE, grade_semantic, grade_semantic_with_retry, judge_prompt,
    validate_diagnostic, DIAGNOSTIC_VERSION)


def _no_symlinks(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError(f'symlink is not allowed: {part}')
    return path


def _safe(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe review path')
    path = root / relative
    _no_symlinks(path)
    return path


def _overlay_tree(overlay):
    _no_symlinks(overlay)
    for current, dirs, files in os.walk(overlay, followlinks=False):
        for name in dirs + files:
            if (Path(current) / name).is_symlink():
                raise ValueError('symlink in review overlay')


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def _args(doc):
    claims = tuple(Claim(c['id'], c['description'], tuple(c.get('evidence', ()))) for c in doc['claims'])
    kwargs = dict(conversation=doc.get('conversation', ()), assess_recovery='Also return "recovery"' in doc['prompt'],
                  assess_interaction='Also return "interaction"' in doc['prompt'],
                  diagnostic_inputs=doc.get('diagnostic_inputs'),
                  diagnostic_version=doc.get('diagnostic_protocol', 'answer-visible-evidence-v1'))
    return claims, kwargs


def _original(root, rel):
    path = _safe(root, rel)
    doc = read_json(path)
    turn = read_json(_safe(root, path.parent.relative_to(root) / 'turn.json'))
    if doc['question'] != turn['prompt'] or doc['answer'] != turn['answer']:
        raise ValueError(f'{rel}: saved answer differs from judgment')
    claims, kwargs = _args(doc)
    protocol = doc.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)
    suffix = doc.get('prompt_suffix', '')
    if suffix not in ('', RETRY_GUIDANCE) or (protocol == LEGACY_JUDGE_PROTOCOL and suffix):
        raise ValueError(f'{rel}: invalid original prompt suffix')
    if judge_prompt(doc['question'], doc['answer'], claims, judge_protocol=protocol, **kwargs) + suffix != doc['prompt']:
        raise ValueError(f'{rel}: original prompt mismatch')
    call_path = _safe(root, Path('model-calls') / Path(doc['model_call']).name)
    call = read_json(call_path)
    if call.get('status') != 'ok' or call.get('text') != doc['response'] or call.get('request', {}).get('messages') != [{'role':'user','content':doc['prompt']}]:
        raise ValueError(f'{rel}: original model call mismatch')
    if doc.get('model_call_hash') is not None:
        digest = hashlib.sha256(json.dumps(call, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != doc['model_call_hash']: raise ValueError(f'{rel}: original model call hash mismatch')
    return doc, call_path


def prepare(root, overlay):
    root, overlay = _no_symlinks(root), _no_symlinks(overlay)
    if overlay.parent != root / 'reviews':
        raise ValueError('overlay must be a direct child of the run reviews directory')
    rows = []
    paths = sorted((root / 'repeat-1').glob('*/turns/*/judgment.json'))
    paths += sorted((root / 'repeat-1').glob('*/turns/*/followups/**/judgment.json'))
    if not paths: raise ValueError('no saved judgments')
    for path in paths:
        rel = path.relative_to(root).as_posix()
        doc, call = _original(root, rel)
        contract = _safe(root, Path('evaluations') / doc['checkpoint'] / 'contract.json')
        if not contract.exists(): raise ValueError(f'{rel}: missing frozen contract')
        frozen = read_json(contract)
        by_id = {c['id']:c for c in frozen['claims']}
        if any(c['id'] not in by_id or c['description'] != by_id[c['id']]['statement'] or
               c.get('evidence', []) != by_id[c['id']].get('evidence', []) for c in doc['claims']):
            raise ValueError(f'{rel}: claims differ from frozen contract')
        files = [path, path.parent / 'turn.json', contract, call]
        rows.append({'judgment':rel, 'files':{f.relative_to(root).as_posix():_hash(f) for f in files}})
    _write(overlay / 'manifest.json', {'version':'rejudge-overlay-v1', 'judge_protocol':DATE_SCOPED_JUDGE_PROTOCOL,
                                      'source_root':os.path.relpath(root, overlay), 'cases':rows})
    return len(rows)


def _manifest(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    manifest = read_json(_safe(overlay, 'manifest.json'))
    if manifest.get('version') != 'rejudge-overlay-v1' or manifest.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL:
        raise ValueError('unsupported overlay')
    if manifest.get('source_root') != '../..' or overlay.parent.name != 'reviews':
        raise ValueError('overlay source must be its containing run')
    root = _no_symlinks(overlay.parent.parent)
    expected = sorted(p.relative_to(root).as_posix() for p in (root/'repeat-1').glob('*/turns/*/judgment.json'))
    expected += sorted(p.relative_to(root).as_posix() for p in (root/'repeat-1').glob('*/turns/*/followups/**/judgment.json'))
    if [c['judgment'] for c in manifest['cases']] != expected: raise ValueError('saved judgment set changed')
    for case in manifest['cases']:
        doc, call = _original(root, case['judgment'])
        path = _safe(root, case['judgment'])
        contract = _safe(root, Path('evaluations') / doc['checkpoint'] / 'contract.json')
        expected_files = {p.relative_to(root).as_posix() for p in (path, path.parent/'turn.json', contract, call)}
        if set(case['files']) != expected_files: raise ValueError('invalid original file inventory')
        for path, digest in case['files'].items():
            if _hash(_safe(root, path)) != digest: raise ValueError(f'original file changed: {path}')
    return manifest, root


def _case_dir(overlay, rel):
    return overlay / 'cases' / Path(rel).parent.relative_to('repeat-1')


def _check_stop(overlay):
    check_stop(overlay/'STOP')


def _resume_retry(overlay, directory, doc, claims, kwargs, judge):
    """Resume only a saved, malformed first attempt after validating its binding."""
    first = read_json(directory/'attempt-1.json')
    expected = judge_prompt(doc['question'], doc['answer'], claims, judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
    call = read_json(overlay/'model-calls'/Path(first['model_call']).name)
    if (first.get('prompt') != expected or first.get('prompt_suffix') != '' or
        first.get('checkpoint') != doc['checkpoint'] or first.get('question') != doc['question'] or
        first.get('answer') != doc['answer'] or
        first.get('claims') != [{'id':c.id,'description':c.description,'evidence':list(c.evidence)} for c in claims] or
        first.get('conversation') != list(kwargs.get('conversation', ())) or
        (kwargs.get('diagnostic_inputs') is not None and first.get('diagnostic_inputs') != kwargs['diagnostic_inputs']) or
        first.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or call.get('status') != 'ok' or
        call.get('text') != first.get('response') or
        call.get('request',{}).get('messages') != [{'role':'user','content':expected}]):
        raise ValueError('cannot resume changed first attempt')
    try:
        grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims,
                       lambda _: first['response'], judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
    except BenchmarkContractError as exc:
        raise ValueError('cannot retry a benchmark contract error') from exc
    except ValueError:
        pass
    else:
        raise ValueError('cannot retry a valid response')
    _check_stop(overlay)
    return grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims, judge,
        record_path=directory/'attempt-2.json', judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL,
        prompt_suffix=RETRY_GUIDANCE, **kwargs)


def run(overlay, judge):
    overlay = _no_symlinks(overlay)
    manifest, root = _manifest(overlay)
    for case in manifest['cases']:
        try: _check_stop(overlay)
        except OperatorStop: return {'state':'stopped', 'reason':'operator stop requested'}
        directory = _case_dir(overlay, case['judgment'])
        if (directory/'result.json').exists(): continue
        doc, _ = _original(root, case['judgment'])
        claims, kwargs = _args(doc)
        try:
            if (directory/'attempt-1.json').exists() and not (directory/'attempt-2.json').exists():
                grade = _resume_retry(overlay, directory, doc, claims, kwargs, judge)
            else:
                grade = grade_semantic_with_retry(doc['checkpoint'], doc['question'], doc['answer'], claims, judge,
                    attempt_dir=directory, before_retry=lambda:_check_stop(overlay), judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
        except OperatorStop:
            return {'state':'stopped', 'reason':'operator stop requested'}
        except ValueError as exc:
            if not (directory/'attempt-1.json').exists() or (not isinstance(exc, BenchmarkContractError) and
                    not (directory/'attempt-2.json').exists()): raise
            _write(directory/'result.json', {'judge_protocol':DATE_SCOPED_JUDGE_PROTOCOL,
                'original_judgment':case['judgment'], 'state':'ungraded', 'error_type':type(exc).__name__})
        else:
            _write(directory/'result.json', {'judge_protocol':DATE_SCOPED_JUDGE_PROTOCOL,
                'original_judgment':case['judgment'], 'state':'graded', 'grade':asdict(grade)})
        try: _check_stop(overlay)
        except OperatorStop: return {'state':'stopped', 'reason':'operator stop requested'}
    return {'state':'complete'}


def audit(overlay):
    overlay = _no_symlinks(overlay)
    manifest, root = _manifest(overlay)
    for case in manifest['cases']:
        directory = _case_dir(overlay, case['judgment'])
        result = read_json(directory/'result.json')
        doc, _ = _original(root, case['judgment'])
        claims, kwargs = _args(doc)
        if result.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or result.get('original_judgment') != case['judgment']:
            raise ValueError('result identity mismatch')
        first, second = directory/'attempt-1.json', directory/'attempt-2.json'
        attempts = [first] + ([second] if second.exists() else [])
        for i, path in enumerate(attempts):
            attempt = read_json(path)
            suffix = RETRY_GUIDANCE if i else ''
            expected = judge_prompt(doc['question'], doc['answer'], claims, judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs) + suffix
            if attempt.get('prompt') != expected or attempt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or attempt.get('prompt_suffix') != suffix:
                raise ValueError('attempt prompt mismatch')
            model_path = overlay/'model-calls'/Path(attempt['model_call']).name
            call = read_json(model_path)
            if call.get('status') != 'ok' or call.get('text') != attempt['response'] or call.get('request',{}).get('messages') != [{'role':'user','content':expected}]:
                raise ValueError('attempt model call mismatch')
            if i == 0 and len(attempts) == 2:
                try:
                    grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims, lambda _:attempt['response'], judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
                except BenchmarkContractError as exc:
                    raise ValueError('retry followed benchmark contract error') from exc
                except ValueError:
                    pass
                else: raise ValueError('retry followed a valid initial response')
        try:
            actual = grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims,
                lambda _:read_json(attempts[-1])['response'], judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
        except ValueError as exc:
            if (result.get('state') != 'ungraded' or result.get('error_type') != type(exc).__name__ or
                    not isinstance(exc, BenchmarkContractError) and len(attempts) != 2):
                raise ValueError('ungraded result mismatch') from exc
        else:
            if result.get('state') != 'graded' or asdict(actual) != result.get('grade'):
                raise ValueError('reviewed grade mismatch')
    return len(manifest['cases'])


def publish_audit(overlay):
    """Write a receipt only after all controls and all cases validate offline."""
    overlay = _no_symlinks(overlay)
    audit_controls(overlay)
    count = audit(overlay)
    files = {p.relative_to(overlay).as_posix():_hash(p) for p in sorted(overlay.rglob('*.json'))
             if p.is_file() and p.name != 'audit.json'}
    _write(overlay/'audit.json', {'state':'pass', 'version':'rejudge-overlay-v1',
        'judge_protocol':DATE_SCOPED_JUDGE_PROTOCOL, 'case_count':count, 'files':files})
    return count


def validate_published_audit(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    receipt = read_json(overlay/'audit.json')
    if (receipt.get('state') != 'pass' or receipt.get('version') != 'rejudge-overlay-v1' or
        receipt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL):
        raise ValueError('invalid overlay audit receipt')
    actual = {p.relative_to(overlay).as_posix():_hash(p) for p in sorted(overlay.rglob('*.json'))
              if p.is_file() and p.name != 'audit.json'}
    if actual != receipt.get('files'): raise ValueError('overlay changed after audit')
    audit_controls(overlay)
    count = audit(overlay)
    if count != receipt.get('case_count'): raise ValueError('overlay case count mismatch')
    return count


def _control_specs():
    claim = Claim('day', 'The upcoming dinner is on Saturday.', ('source-1',))
    text = '〔E2〕 Sat Oct 24  "Dinner", 7pm — confirmed · with Ben'
    inputs = {'answer_visible':[{'id':'prefetch','text':text,'truncated':False}],
        'source_evidence':[{'id':'source-1','text':'Dinner on Saturday October 24.', 'truncated':False}],
        'source_scope':'partial: expected-claim citations only', 'missing_source_ids':[],
        'answer_visible_scope':'provider evidence returned to Hermes: captured prefetch and direct tool results only; user prompts, tool arguments and background traces excluded',
        'per_item_character_limit':16000}
    question = '[Simulated now: 2026-10-19T10:30:00-04:00.] When is upcoming dinner?'
    return [(name, question, answer, (claim,), inputs, expected) for name, answer, expected in [
        ('right_date', 'Saturday October 24.', 'correct'),
        ('past_same_weekday', 'Saturday October 17.', 'misrepresented')]]


def controls(overlay, judge):
    overlay = _no_symlinks(overlay)
    _manifest(overlay)
    for name, question, answer, claims, inputs, expected in _control_specs():
        try: _check_stop(overlay)
        except OperatorStop: return {'state':'stopped', 'reason':'operator stop requested'}
        directory = overlay/'controls'/name
        if (directory/'result.json').exists(): continue
        kwargs = {'diagnostic_version':DIAGNOSTIC_VERSION, 'diagnostic_inputs':inputs}
        try:
            if (directory/'attempt-1.json').exists() and not (directory/'attempt-2.json').exists():
                doc={'checkpoint':name, 'question':question, 'answer':answer}
                grade = _resume_retry(overlay, directory, doc, claims, kwargs, judge)
            else:
                grade = grade_semantic_with_retry(name, question, answer, claims, judge, attempt_dir=directory,
                    before_retry=lambda:_check_stop(overlay), judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
        except OperatorStop:
            return {'state':'stopped', 'reason':'operator stop requested'}
        bad = {'claims':[{'id':'day','status':'supported','reason':'Altered marker',
            'citations':[{'id':'prefetch','quote':'〈E2〉'}]}], 'extra_assertions':[]}
        invalid_rejected = validate_diagnostic(bad, claims, answer, inputs, DIAGNOSTIC_VERSION)['state'] == 'ungraded'
        passed = (grade.slots[0].verdict == expected and grade.evidence_diagnostic.get('state') == 'complete'
                  and grade.evidence_diagnostic['claims'][0]['status'] == 'supported' and invalid_rejected)
        _write(directory/'result.json', {'grade':asdict(grade), 'passed':passed, 'invalid_quote_rejected':invalid_rejected})
        if not passed: raise ValueError(f'control failed: {name}')
        try: _check_stop(overlay)
        except OperatorStop: return {'state':'stopped', 'reason':'operator stop requested'}
    audit_controls(overlay)
    return {'state':'complete'}


def audit_controls(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    for name, question, answer, claims, inputs, expected in _control_specs():
        directory = overlay/'controls'/name
        result = read_json(directory/'result.json')
        paths = [directory/'attempt-1.json'] + ([directory/'attempt-2.json'] if (directory/'attempt-2.json').exists() else [])
        kwargs = dict(judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, diagnostic_version=DIAGNOSTIC_VERSION, diagnostic_inputs=inputs)
        for i, path in enumerate(paths):
            attempt = read_json(path)
            suffix = RETRY_GUIDANCE if i else ''
            prompt = judge_prompt(question, answer, claims, **kwargs) + suffix
            call = read_json(overlay/'model-calls'/Path(attempt['model_call']).name)
            if (attempt.get('prompt') != prompt or attempt.get('prompt_suffix') != suffix or
                attempt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or call.get('status') != 'ok' or
                call.get('text') != attempt.get('response') or
                call.get('request',{}).get('messages') != [{'role':'user','content':prompt}]):
                raise ValueError('control receipt mismatch')
            if i == 0 and len(paths) == 2:
                try: grade_semantic(name, question, answer, claims, lambda _:attempt['response'], **kwargs)
                except BenchmarkContractError as exc: raise ValueError('control retry followed benchmark contract error') from exc
                except ValueError: pass
                else: raise ValueError('control retry followed valid response')
        grade = grade_semantic(name, question, answer, claims, lambda _:read_json(paths[-1])['response'], **kwargs)
        bad = {'claims':[{'id':'day','status':'supported','reason':'Altered marker',
            'citations':[{'id':'prefetch','quote':'〈E2〉'}]}], 'extra_assertions':[]}
        if (not result.get('passed') or not result.get('invalid_quote_rejected') or asdict(grade) != result.get('grade') or
            grade.slots[0].verdict != expected or grade.evidence_diagnostic.get('state') != 'complete' or
            grade.evidence_diagnostic['claims'][0]['status'] != 'supported' or
            validate_diagnostic(bad, claims, answer, inputs, DIAGNOSTIC_VERSION)['state'] != 'ungraded'):
            raise ValueError(f'control failed: {name}')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare','controls','run','audit'))
    parser.add_argument('overlay', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.source is None: parser.error('prepare requires --source')
        print(json.dumps({'cases':prepare(args.source,args.overlay)})); return
    if args.action == 'audit':
        print(json.dumps({'state':'pass','cases':publish_audit(args.overlay)})); return
    from .gateway_client import client_settings, require_live_cost_boundary
    require_live_cost_boundary()
    base, key = client_settings('pacbench-judge')
    client = ModelClient(key=key, model='muse-spark-1.3-contributor',
                         url=base+'/chat/completions', record_dir=args.overlay/'model-calls')
    def judge(prompt):
        require_live_cost_boundary()
        return client(prompt)
    if args.action == 'controls': print(json.dumps(controls(args.overlay,judge)))
    else:
        audit_controls(args.overlay)
        print(json.dumps(run(args.overlay,judge)))


if __name__ == '__main__': main()
