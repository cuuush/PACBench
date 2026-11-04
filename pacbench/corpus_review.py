"""Independent realism review bound to the actual frozen source and expectations."""
from __future__ import annotations

import json
from pathlib import Path

from .followups import digest, save
from .semantic_contract import _dialogue

VERSION = 'source-realism-v5'
SUPPORTED_VERSIONS = {'source-realism-v3', 'source-realism-v4', VERSION}


def review_intent(resolved):
    return {'scenes': [{k: s[k] for k in ('id', 'at', 'thread', 'scene', 'facts')}
                       for s in resolved['scenes']], 'checks': resolved['checks']}


def review_prompt(messages, resolved, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unsupported corpus review version')
    return (
        'Audit this synthetic personal-message corpus and its intended checks. '
        'Read the actual messages; authored intentions do not establish facts. '
        'Check chronology, natural relative-day references, cross-thread/person '
        'continuity, and whether each expected answer follows objectively from '
        'the messages available at question time. Distinguish quotes, jokes, '
        'suggestions, rejected changes and confirmed plans. Flag contradictory '
        'or ambiguous ground truth, implausible conversation, and tests of '
        'assistant style rather than memory. Do not demand explicit repetition '
        'where ordinary conversational context resolves a reference. '
        'Return JSON {"consistent":true,"expectations_supported":true,'
        '"natural_enough":true,"issues":[],"current_upcoming":[],"notes":"..."}. '
        'If something is wrong, set the relevant flag false and describe the '
        'issue; do not repair or excuse it to satisfy the authored expectation.\n'
        + ('Scripted public user messages are real planned inputs when delivered: '
           'they may correct a plan or introduce a fact. Distinguish those inputs '
           'from private expectations and invented assistant replies. Honor requires '
           'and depends_on for conditional inputs. A behavior check may accept an '
           'ordinary acknowledgement; only a later fresh-session question tests '
           'persistence. Do not assume the assistant successfully wrote memory.\n'
           if version in ('source-realism-v4', VERSION) else '') +
        ('For every relevant relative day or week, use the written timestamp of '
         'the message containing it, including each reply separately. Resolve '
         'rescheduling direction and date from the messages themselves, not '
         'from the authored intention. Reject conflicting dates and references '
         'that leave materially different dates plausible, regardless of the '
         'authored intention. Earlier or quoted '
         'messages retain their own written clock. Explicit dates and ordinary '
         'context may resolve a reference; do not invent an anchor to make it fit.\n'
         if version == 'source-realism-v5' else '') +
        f'Authored intentions and questions:\n{json.dumps(review_intent(resolved), ensure_ascii=False)}\n'
        f'Actual source:\n{_dialogue(messages)}')


def review_corpus(messages, resolved, model, root: Path):
    # ModelClient reuses only an exact successful request. A changed source or
    # expectation produces a new request, including on preparation resume.
    result = model(review_prompt(messages, resolved))
    verdict = json.loads(result.text)
    passed = (isinstance(verdict, dict) and
              all(verdict.get(k) is True for k in
                  ('consistent', 'expectations_supported', 'natural_enough')) and
              verdict.get('issues') == [])
    receipt = {'state': 'pass' if passed else 'fail', 'review_version': VERSION,
               'source_hash': digest(messages), 'intent_hash': digest(review_intent(resolved)),
               'review': verdict, 'model_call': result.record_path, 'usage': result.usage}
    save(root / 'realism-review.json', receipt)
    if not passed:
        raise ValueError(f'Corpus realism review rejected: {verdict}')
    return receipt
