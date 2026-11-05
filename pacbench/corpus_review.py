"""Independent realism review bound to the actual frozen source and expectations."""
from __future__ import annotations

import json
from pathlib import Path

from .followups import digest, save
from .semantic_contract import _dialogue

VERSION = 'source-realism-v9'
SOURCE_BLIND = {'source-realism-v7','source-realism-v8',VERSION}
SUPPORTED_VERSIONS = {'source-realism-v3', 'source-realism-v4', 'source-realism-v5', 'source-realism-v6', *SOURCE_BLIND}


def review_intent(resolved):
    return {'scenes': [{k: s[k] for k in ('id', 'at', 'thread', 'scene', 'facts')}
                       for s in resolved['scenes']], 'checks': resolved['checks']}


def review_prompt(messages, resolved, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unsupported corpus review version')
    if version == 'source-realism-v9':
        return _v8_prompt(messages,resolved) + (
            '\nJudge the intended state assuming memory retained prior delivered '
            'inputs, including across fresh sessions. A definite user report of '
            'a changed plan updates that state without requiring another person '
            'to confirm it. Whether a provider actually saved the update is what '
            'later recall tests; it is not a source-answerability requirement.')
    if version == 'source-realism-v8':return _v8_prompt(messages,resolved)
    if version == 'source-realism-v7':
        return _v7_prompt(messages, resolved)
    if version == 'source-realism-v6':
        return _v6_prompt(messages, resolved)
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
           if version in ('source-realism-v4', 'source-realism-v5') else '') +
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


def _v6_prompt(messages, resolved):
    from .document_ingestion import daily_batches, document_text
    batches = daily_batches(messages, resolved.get('timezone', 'America/New_York'))
    source = '\n'.join(document_text(batch, max(m['collected_at'] for m in batch))
                       for _, batch in batches)
    return (
        'Independently audit this complete raw message corpus. Read the messages '
        'first and establish what they say before comparing any authored intentions. '
        'Every message body is retained, including noise and quoted email history. '
        'The timestamp beside each message is its own written time. Resolve every '
        'relevant relative day and week using that timestamp, including replies and '
        'quoted messages. Identify materially plausible alternative dates; do not '
        'invent a later or earlier anchor from the author’s expectation. A proposal '
        'to move an event to a weekday can refer to the next occurrence from the '
        'message date or to a weekday in the event’s week unless the source itself '
        'resolves it. Distinguish confirmed plans, quotes, jokes, rejected changes '
        'and tentative ideas. Preserve ordinary conversational context.\n'
        f'Actual source, chronological daily transcripts:\n{source}\n'
        'Now evaluate each check using only messages available at its own time. '
        'Authored scenes, facts and expectations are hypotheses, not evidence or '
        'anchors. Scripted public user turns are real inputs when delivered; '
        'honor requires and depends_on, and never assume assistant memory writes. '
        'Report one assessment per check ID. Each assessment must contain '
        'check_id, event_date (a concrete date or not applicable), source_evidence '
        '(a nonempty array of {written_at, thread, quote} using exact source text), '
        'alternatives (a nonempty array of plausible alternatives or the string '
        'none in an array), and ambiguous (boolean). A check is ambiguous if '
        'materially different dates or outcomes remain plausible. Set consistent '
        'or expectations_supported false for contradictions, unsupported claims '
        'or ambiguity; set natural_enough false for implausible conversation. '
        'Return only JSON: {"consistent":true,"expectations_supported":true,'
        '"natural_enough":true,"issues":[],"assessments":[{"check_id":"...",'
        '"event_date":"...","source_evidence":[{"written_at":"...",'
        '"thread":"...","quote":"..."}],"alternatives":["none"],'
        '"ambiguous":false}],"notes":"..."}. Explain actual issues without '
        'repairing them to satisfy the expectation.\n'
        f'Authored intentions and timestamped questions:\n{json.dumps(review_intent(resolved), ensure_ascii=False)}')


def _v8_prompt(messages,resolved):
    return _v7_prompt(messages,resolved,include_turn_metadata=True) + (
        '\nExact citations may also quote the timestamped public user inputs '
        'above. Later inputs cannot justify earlier answers; conditional inputs '
        'are not established history. User instructions do not prove assistant '
        'persistence or completed external actions.')


def _v7_prompt(messages, resolved, *, include_turn_metadata=False):
    from .document_ingestion import daily_batches, document_text
    batches = daily_batches(messages, resolved.get('timezone', 'America/New_York'))
    source = '\n'.join(document_text(batch, max(m['collected_at'] for m in batch))
                       for _, batch in batches)
    keys=('id','at','prompt','session','kind','trigger','depends_on','requires') if include_turn_metadata else ('id','at','prompt')
    questions = [{k:check[k] for k in keys if k in check} for check in resolved['checks']]
    return (
        'Independently audit this complete raw personal-message corpus and the '
        'answerability of its public questions. Every message body is retained, '
        'including noise and quoted email history. The timestamp beside each '
        'message is its own written time. Resolve relative days and weeks using '
        'that timestamp, including replies and quoted messages. Identify materially '
        'plausible alternative dates and outcomes from the source. Distinguish '
        'confirmed plans, quotes, jokes, rejected changes and tentative ideas. '
        'Preserve ordinary conversational context.\n'
        f'Actual source, chronological daily transcripts:\n{source}\n'
        'For each timestamped question use only information available at its '
        'question time. Evaluate source coherence, naturalness and whether the '
        'question has a determinate answer. Public user questions are actual '
        'inputs when delivered; do not assume assistant memory writes. Report one '
        'assessment per question ID with event_date (a concrete date or not '
        'applicable), source_evidence (a nonempty array of {written_at, quote} '
        'using exact source text and timestamp), alternatives (a nonempty array '
        'of plausible alternatives or the string none in an array), and '
        'ambiguous (boolean). Set consistent false for contradictions, '
        'questions_answerable false for material ambiguity or unsupported answers, '
        'and natural_enough false for implausible conversation. Return only JSON: '
        '{"consistent":true,"questions_answerable":true,"natural_enough":true,'
        '"issues":[],"assessments":[{"check_id":"...","event_date":"...",'
        '"source_evidence":[{"written_at":"...","quote":"..."}],'
        '"alternatives":["none"],"ambiguous":false}],"notes":"..."}. '
        'Explain any issue.\n'
        f'Timestamped public questions:\n{json.dumps(questions, ensure_ascii=False)}')


def validation_errors(verdict, messages, resolved, *, version=VERSION):
    """Verify exact citations; v8 also accepts delivered public user inputs."""
    errors = []
    def add(path, code):
        errors.append({'path': path, 'code': code})
    if not isinstance(verdict, dict):
        return [{'path': 'review', 'code': 'not_object'}]
    checks = resolved['checks']
    candidates=list(messages)
    if version == 'source-realism-v8':
        candidates.extend({'written_at':r['at'],'collected_at':r['at'],'text':r['prompt']}
                          for r in checks if r.get('trigger') != 'clarification')
    conditional={r['id'] for r in checks if r.get('trigger')=='clarification'}
    for r in checks:
        dependencies=[*r.get('requires',[]),*([r['depends_on']] if r.get('depends_on') else [])]
        if any(d in conditional for d in dependencies):conditional.add(r['id'])
    ids = [c['id'] for c in checks]
    if len(set(ids)) != len(ids): add('checks', 'duplicate_id')
    by_id = {c['id']: c for c in checks}
    assessments = verdict.get('assessments')
    if not isinstance(assessments, list):
        add('assessments', 'not_array')
        return errors
    if len(assessments) != len(checks): add('assessments', 'wrong_count')
    from datetime import datetime
    found = []
    for index, item in enumerate(assessments):
        if not isinstance(item, dict):
            add(f'assessments[{index}]', 'not_object')
            continue
        check_id = item.get('check_id')
        path = f'assessments[{check_id}]' if isinstance(check_id, str) else f'assessments[{index}]'
        if isinstance(check_id, str): found.append(check_id)
        if not isinstance(check_id, str) or check_id not in by_id:
            add(path + '.check_id', 'unknown_id')
        if not isinstance(item.get('event_date'), str) or not item['event_date'].strip():
            add(path + '.event_date', 'missing_text')
        if item.get('ambiguous') is not False:
            add(path + '.ambiguous', 'not_false')
        alternatives = item.get('alternatives')
        if not isinstance(alternatives, list) or not alternatives or not all(isinstance(x, str) and x.strip() for x in alternatives):
            add(path + '.alternatives', 'invalid_array')
        evidence = item.get('source_evidence')
        if not isinstance(evidence, list) or not evidence:
            add(path + '.source_evidence', 'invalid_array')
            continue
        for i, entry in enumerate(evidence):
            evidence_path = f'{path}.source_evidence[{i}]'
            if not isinstance(entry, dict) or not isinstance(entry.get('quote'), str) or not entry['quote'].strip():
                add(evidence_path, 'missing_quote')
                continue
            available=candidates
            if version == 'source-realism-v9':
                prior=checks[:next((i+1 for i,c in enumerate(checks) if c['id']==check_id),0)]
                available=[*messages,*({'written_at':r['at'],'collected_at':r['at'],'text':r['prompt']}
                    for r in prior if r['id'] not in conditional)]
            matches = [m for m in available if entry.get('written_at') == m['written_at'] and entry['quote'] in m['text']]
            if not matches:
                add(evidence_path, 'timestamp_quote_no_match')
            elif len(matches) > 1:
                add(evidence_path, 'timestamp_quote_ambiguous_match')
            elif isinstance(check_id, str) and check_id in by_id and datetime.fromisoformat(matches[0]['collected_at']) > datetime.fromisoformat(by_id[check_id]['at']):
                add(evidence_path, 'source_after_question')
    if len(found) != len(ids) or len(set(found)) != len(found) or set(found) != set(ids):
        add('assessments', 'ids_mismatch')
    return errors


def verdict_passes(verdict, messages, resolved, *, version=VERSION):
    flag = 'questions_answerable' if version in SOURCE_BLIND else 'expectations_supported'
    if not (isinstance(verdict, dict) and
            all(verdict.get(k) is True for k in
                ('consistent', flag, 'natural_enough')) and
            verdict.get('issues') == []):
        return False
    if version in SOURCE_BLIND:
        return not validation_errors(verdict, messages, resolved,version=version)
    if version != 'source-realism-v6':
        return True
    assessments = verdict.get('assessments')
    checks = resolved['checks']
    if not isinstance(assessments, list) or len(assessments) != len(checks):
        return False
    ids = [c['id'] for c in checks]
    if len(set(ids)) != len(ids):
        return False
    from datetime import datetime
    by_id = {c['id']: c for c in checks}
    found = []
    for item in assessments:
        if not isinstance(item, dict): return False
        found.append(item.get('check_id'))
        if item.get('check_id') not in by_id: return False
        if (not isinstance(item.get('event_date'), str) or not item['event_date'].strip()
                or item.get('ambiguous') is not False): return False
        alternatives = item.get('alternatives')
        if not isinstance(alternatives, list) or not alternatives or not all(isinstance(x, str) and x.strip() for x in alternatives): return False
        evidence = item.get('source_evidence')
        if not isinstance(evidence, list) or not evidence: return False
        for e in evidence:
            if not isinstance(e, dict) or not isinstance(e.get('quote'), str) or not e['quote'].strip(): return False
            matches = [m for m in messages if e.get('written_at') == m['written_at']
                       and e['quote'] in m['text']]
            if version == 'source-realism-v6':
                matches = [m for m in matches if e.get('thread') == m['thread']]
                if not matches: return False
            elif len(matches) != 1:
                return False
            if not any(datetime.fromisoformat(m['collected_at']) <=
                       datetime.fromisoformat(by_id[item['check_id']]['at']) for m in matches): return False
    return len(set(found)) == len(found) and set(found) == set(ids)


def review_corpus(messages, resolved, model, root: Path):
    # ModelClient reuses only an exact successful request. A changed source or
    # expectation produces a new request, including on preparation resume.
    result = model(review_prompt(messages, resolved))
    verdict = json.loads(result.text)
    passed = verdict_passes(verdict, messages, resolved)
    receipt = {'state': 'pass' if passed else 'fail', 'review_version': VERSION,
               'source_hash': digest(messages), 'intent_hash': digest(review_intent(resolved)),
               'review': verdict, 'model_call': result.record_path, 'usage': result.usage}
    if VERSION in SOURCE_BLIND:
        receipt['scope'] = 'source-coherence-and-public-question-answerability'
        receipt['validation_errors'] = validation_errors(verdict, messages, resolved)
    save(root / 'realism-review.json', receipt)
    if not passed:
        raise ValueError(f'Corpus realism review rejected: {verdict}')
    return receipt
