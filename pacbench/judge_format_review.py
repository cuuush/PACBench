"""Offline, format-only review of complete grades before incomplete diagnostics.

This never changes a judge's verdict, calls a model, or modifies original run
evidence. It is deliberately not the parser used by historical/native trials.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .evidence_store import read_json
from .rejudge import _args, _hash, _no_symlinks, _original, _overlay_tree, _safe, _write
from .semantic_grade import (BenchmarkContractError, LEGACY_JUDGE_PROTOCOL,
                             audit_native_judge_attempts, grade_semantic)

VERSION = 'judge-diagnostic-isolation-v1'
CORE_FIELDS = {'verdicts', 'recovery', 'interaction', 'benchmark_error'}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonstandard JSON constant')


DECODER = json.JSONDecoder(object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)


def _unfinished_diagnostic(text):
    """Recognize missing container closers only; never repair field contents."""
    stack = []
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            stack.append('}' if char == '{' else ']')
        elif char in '}]':
            if not stack or stack.pop() != char:
                raise ValueError('mismatched diagnostic delimiters')
    if quoted or not stack:
        raise ValueError('diagnostic is not missing container closers only')
    # Used only to recognize the narrow failure shape. The diagnostic is always
    # discarded, never accepted as repaired evidence or a completed model reply.
    candidate = DECODER.decode(text + ''.join(reversed(stack)))
    if not isinstance(candidate, dict):
        raise ValueError('diagnostic must be an object')


def complete_core(response, *, assess_recovery=False, assess_interaction=False):
    """Return exact, complete core values before a final unfinished diagnostic.

    Reject duplicate/unknown keys, truncated strings or scalars, invalid JSON,
    missing required fields and any complete value following the diagnostic.
    """
    if not isinstance(response, str):
        raise ValueError('response must be text')
    text = response.strip()
    if not text.startswith('{'):
        raise ValueError('response must start with an object')
    required = {'verdicts'}
    if assess_recovery:
        required.add('recovery')
    if assess_interaction:
        required.update(('interaction', 'benchmark_error'))
    pos, core = 1, {}
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        key, pos = DECODER.raw_decode(text, pos)
        if not isinstance(key, str) or key in core:
            raise ValueError('invalid or duplicate top-level key')
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if text[pos:pos + 1] != ':':
            raise ValueError('missing field separator')
        pos += 1
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if key == 'evidence_diagnostic':
            if not required <= core.keys():
                raise ValueError('incomplete semantic fields before diagnostic')
            # A complete diagnostic followed by a broken outer object is not
            # this condition. Refuse rather than silently discard later fields.
            try:
                DECODER.raw_decode(text, pos)
            except json.JSONDecodeError:
                _unfinished_diagnostic(text[pos:])
                return core
            raise ValueError('diagnostic is complete or has trailing content')
        if key not in CORE_FIELDS:
            raise ValueError('unknown field before diagnostic')
        core[key], pos = DECODER.raw_decode(text, pos)
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if text[pos:pos + 1] != ',':
            raise ValueError('missing trailing diagnostic')
        pos += 1


def review_response(doc):
    """Replay existing verdicts; format recovery is not a new semantic review."""
    claims, kwargs = _args(doc)
    protocol = doc.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)
    response = doc['response']

    def grade(text):
        return grade_semantic(doc['checkpoint'], doc['question'], doc['answer'],
                              claims, lambda _: text, judge_protocol=protocol, **kwargs)

    try:
        original = grade(response)
    except BenchmarkContractError:
        return {'state': 'ungraded', 'reason': 'original benchmark contract error'}
    except ValueError:
        pass
    else:
        return {'state': 'unchanged', 'grade': asdict(original)}
    if kwargs.get('diagnostic_inputs') is None:
        return {'state': 'ungraded', 'reason': 'no independent diagnostic requested'}
    try:
        core = complete_core(response, assess_recovery=kwargs['assess_recovery'],
                             assess_interaction=kwargs['assess_interaction'])
        recovered = grade(json.dumps(core, ensure_ascii=False))
    except (ValueError, TypeError):
        return {'state': 'ungraded', 'reason': 'not an eligible complete semantic prefix'}
    recovered.evidence_diagnostic = {
        'version': kwargs['diagnostic_version'], 'state': 'ungraded',
        'reason': 'Incomplete trailing diagnostic; no diagnostic content was repaired or accepted'}
    return {'state': 'recovered', 'accepted_fields': list(core),
            'response_sha256': hashlib.sha256(response.encode()).hexdigest(),
            'grade': asdict(recovered)}


def _judgments(root):
    paths = sorted(root.glob('repeat-*/*/turns/*/judgment.json'))
    return paths + sorted(root.glob('repeat-*/*/turns/*/followups/**/judgment.json'))


def build_review(root):
    """Validate every saved judgment, its frozen contract and both attempt receipts."""
    root = _no_symlinks(root)
    paths = _judgments(root)
    if not paths:
        raise ValueError('no saved judgments')
    files, cases = set(), []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        doc, call = _original(root, rel)
        claims, kwargs = _args(doc)
        contract = _safe(root, Path('evaluations') / doc['checkpoint'] / 'contract.json')
        frozen = {c['id']: c for c in read_json(contract)['claims']}
        if any(c.id not in frozen or c.description != frozen[c.id]['statement'] or
               list(c.evidence) != frozen[c.id].get('evidence', []) for c in claims):
            raise ValueError('saved claims differ from frozen contract')
        protocol = doc.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)
        if protocol != LEGACY_JUDGE_PROTOCOL:
            attempts = _safe(root, path.parent.relative_to(root) / 'judgment-attempts')
            for attempt in attempts.iterdir():
                _safe(root, attempt.relative_to(root))
                if attempt.name in ('attempt-1.json', 'attempt-2.json'):
                    _safe(root, Path('model-calls') / Path(read_json(attempt)['model_call']).name)
        audit_native_judge_attempts(path, question=doc['question'], answer=doc['answer'],
                                    claims=claims, judge_protocol=protocol, **kwargs)
        files.update((path, path.parent / 'turn.json', contract, call))
        if protocol != LEGACY_JUDGE_PROTOCOL:
            for attempt in sorted((path.parent / 'judgment-attempts').glob('*.json')):
                files.add(_safe(root, attempt.relative_to(root)))
                files.add(_safe(root, Path('model-calls') / Path(read_json(attempt)['model_call']).name))
        cases.append({'judgment': rel, 'original_judge_protocol': protocol, **review_response(doc)})
    # Shared text participates in the exact original request bindings above.
    # Freeze it as well so changes to its unused chunks are not silently accepted.
    for optional in ('shared-text.json', 'summary.json'):
        if (root / optional).exists():
            files.add(_safe(root, optional))
    return {'version': VERSION, 'source_root': '../..', 'semantic_only': True,
            'retention_recomputed': False,
            'scope': 'All saved judgments; no new inference or semantic reassessment',
            'files': {p.relative_to(root).as_posix(): _hash(p) for p in sorted(files)},
            'cases': cases}


def _counts(review):
    return {state: sum(c['state'] == state for c in review['cases'])
            for state in ('unchanged', 'recovered', 'ungraded')}


def audit_review(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    if overlay.parent.name != 'reviews':
        raise ValueError('overlay must be inside the run reviews directory')
    if {p.name for p in overlay.iterdir()} - {'review.json', 'audit.json'}:
        raise ValueError('unexpected review files')
    saved = read_json(overlay / 'review.json')
    current = [p.relative_to(overlay.parent.parent).as_posix()
               for p in _judgments(overlay.parent.parent)]
    if [case['judgment'] for case in saved.get('cases', [])] != current:
        raise ValueError('saved judgment set changed')
    actual = build_review(overlay.parent.parent)
    if saved != actual:
        raise ValueError('review or original evidence changed')
    receipt = {'version': VERSION, 'state': 'pass', 'review_sha256': _hash(overlay / 'review.json'),
               'case_count': len(saved['cases']), **_counts(saved)}
    if (overlay / 'audit.json').exists() and read_json(overlay / 'audit.json') != receipt:
        raise ValueError('review audit receipt changed')
    return receipt


def create_review(root, overlay):
    root, overlay = _no_symlinks(root), _no_symlinks(overlay)
    if overlay.parent != root / 'reviews':
        raise ValueError('overlay must be a direct child of the run reviews directory')
    if overlay.exists():
        raise FileExistsError('review overlay already exists')
    review = build_review(root)
    _write(overlay / 'review.json', review)
    receipt = audit_review(overlay)
    _write(overlay / 'audit.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'audit'))
    parser.add_argument('overlay', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    if args.action == 'create':
        if args.source is None:
            parser.error('create requires --source')
        result = create_review(args.source, args.overlay)
    else:
        result = audit_review(args.overlay)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
