"""Import a complete, explicitly selected judge result from a local recorder."""
import hashlib
import json
import re
from dataclasses import dataclass

from pacbench.judging.grade import Claim, grade_semantic
from pacbench.judging.retained_week import read_bytes

SCHEMA = 'pacbench-regrade-response-journal-v1'
COVERAGE_SCHEMA = 'pacbench-regrade-response-journal-v2'
FORMAT = 'semantic-verdicts-v1'
IDENTITY_FIELDS = {'model', 'route', 'endpoint', 'fallback', 'response_format'}


@dataclass(frozen=True)
class RecordedVerdict:
    cell: str
    attempt: str
    response: str
    result: dict


@dataclass(frozen=True)
class JournalResult:
    provenance: dict
    verdicts: tuple[RecordedVerdict, ...]


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON key in judge evidence')
        obj[key] = value
    return obj


def _constant(_value):
    raise ValueError('nonstandard JSON constant in judge evidence')


def _object(text):
    result = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(result, dict):
        raise ValueError('judge evidence must be a JSON object')
    return result


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value))


def _require_complete_arms(plan):
    if not plan['items']:
        raise ValueError('no verified turns to regrade')
    for arm, row in plan['coverage'].items():
        binding = row.get('binding', {})
        if (row['state'] != 'verified-retained-inputs'
                or row['completed_turns'] != row['expected_turns']
                or row['verified_pending_completion']
                or not row['expected_criteria']
                or row['completed_criteria'] != row['expected_criteria']
                or not (row['execution_sealed'] or binding.get('journal_completed'))):
            raise ValueError(f'incomplete retained answers or execution evidence for arm {arm}')
        if row['source_provenance'].get('missing_evidence'):
            raise ValueError(f'missing original intake evidence for arm {arm}')


def _result(row, item, judge, *, keep_invalid):
    cell = item['id']
    request = row['request']
    receipt = row['receipt']
    routing = {field: judge[field] for field in ('model', 'route', 'endpoint')}
    if (row['input_sha256'] != item['input_sha256']
            or row['prompt_sha256'] != item['prompt_sha256']
            or request != {**routing, 'fallback': judge['fallback'], 'prompt': item['prompt']}
            or any(receipt.get(field) != value for field, value in routing.items())
            or not _identifier(receipt.get('request_id'))):
        raise ValueError(f'wrong judge request, route or frozen input for {cell}')
    if receipt.get('status') != 'complete':
        raise ValueError(f'incomplete recorded judge response for {cell}')
    response = row['response']
    if not isinstance(response, str) or receipt.get('response_sha256') != _sha(response):
        raise ValueError(f'recorded judge response hash differs for {cell}')
    try:
        parsed = _object(response)
        if set(parsed) != {'verdicts'} or not isinstance(parsed['verdicts'], list) or any(
                not isinstance(verdict, dict) or set(verdict) != {'id', 'verdict', 'reason'}
                for verdict in parsed['verdicts']):
            raise ValueError('unsupported semantic verdict object')
        turn = item['saved']['turn']
        claims = tuple(Claim(c['id'], c['description'], tuple(c['evidence'])) for c in turn['claims'])
        grade = grade_semantic(turn['id'], turn['prompt'], item['saved']['surface']['answer'],
                               claims, lambda _: response, **item['options'])
        state, criteria = 'parsed-recorded-response', [vars(slot) for slot in grade.slots]
    except (ValueError, TypeError, KeyError, RecursionError):
        if not keep_invalid:
            raise ValueError(f'unsupported or incomplete semantic verdicts for {cell}') from None
        state, criteria = 'invalid-output', None
    return RecordedVerdict(cell, row['attempt_id'], response, {
        'state': state, 'criteria': criteria,
        'request_id': receipt['request_id'],
        'prompt_sha256': row['prompt_sha256'],
        'input_sha256': row['input_sha256'],
        'response_sha256': receipt['response_sha256'],
        'receipt_verified': False,  # Journal integrity does not verify billing or upstream authenticity.
    })


def load(path, plan, selector, expected_sha256):
    """Validate a whole selection before the caller opens or changes its overlay."""
    raw, sha = read_bytes(path)
    if sha != expected_sha256:
        raise ValueError('response journal SHA-256 differs from the expected recording')
    if not raw.endswith(b'\n'):
        raise ValueError('incomplete response journal tail')
    head, *lines = raw.decode('utf-8').split('\n')[:-1]
    header = _object(head)
    schema = header.get('schema')
    if schema not in (SCHEMA, COVERAGE_SCHEMA):
        raise ValueError('unsupported recorded response journal schema')
    if (header.get('plan_id'), header.get('revision')) != (plan['plan_id'], plan['revision']):
        raise ValueError('response journal belongs to another frozen plan or rubric revision')
    lanes = header.get('judges')
    judge = lanes.get(selector) if isinstance(lanes, dict) else None
    if not _identifier(selector) or not isinstance(judge, dict) or set(judge) != IDENTITY_FIELDS:
        raise ValueError('selected judge has no recorded identity')
    if (judge['response_format'] != FORMAT or type(judge['fallback']) is not bool
            or not all(_identifier(judge[field]) for field in ('model', 'route', 'endpoint'))):
        raise ValueError('unsupported selected judge identity or result format')
    for field in ('model', 'route', 'fallback'):
        pin = plan['judge'].get(field)
        if pin is not None and judge[field] != pin:
            raise ValueError('recorded judge differs from the selected model/route revision')

    _require_complete_arms(plan)
    items = {item['id']: item for item in plan['items']}
    records, attempts, requests = {}, set(), set()
    for line in lines:
        row = _object(line)
        if row.get('judge') not in lanes:
            raise ValueError('response row has no declared judge')
        if row['judge'] != selector:
            continue
        cell, attempt = row.get('cell'), row.get('attempt_id')
        if not isinstance(cell, str) or cell not in items:
            raise ValueError('recorded judge response names an absent or ungradable turn')
        if cell in records or not _identifier(attempt) or attempt in attempts:
            raise ValueError(f'duplicate cell or attempt in selected judge recording for {cell}')
        if not isinstance(row.get('request'), dict) or not isinstance(row.get('receipt'), dict):
            raise ValueError(f'missing recorded request or response receipt for {cell}')
        try:
            recorded = _result(row, items[cell], judge, keep_invalid=schema == COVERAGE_SCHEMA)
        except (KeyError, TypeError):
            raise ValueError(f'incomplete judge evidence for {cell}') from None
        request_id = recorded.result['request_id']
        if request_id in requests:
            raise ValueError(f'duplicate recorded request ID for {cell}')
        attempts.add(attempt)
        requests.add(request_id)
        records[cell] = recorded
    if len(records) != len(items):
        raise ValueError(f'selected judge is missing {len(items) - len(records)} of {len(items)} planned responses')
    return JournalResult({'schema': schema, 'judge_id': selector, **judge, 'journal_sha256': sha,
                          'coverage': len(records)},
                         tuple(records[cell] for cell in items))
