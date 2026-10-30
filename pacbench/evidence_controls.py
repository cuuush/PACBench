"""Opt-in live controls for the answer evidence judge; caller supplies gateway model."""
from pathlib import Path

from .semantic_grade import Claim, grade_semantic, validate_diagnostic, DIAGNOSTIC_VERSION


def run_controls(judge, output: Path):
    """Run three independent controls, preserving the actual judge request and response.

    This invokes the supplied model three times; the caller must enforce gateway spending
    and access checks before use. These are controls, never provider scores.
    """
    claim = Claim('time', 'The meeting is Friday at 7pm.', ('source-1',))
    inputs = {'answer_visible': [{'id': 'tool-result-1', 'text': 'The meeting is Friday at 7pm.', 'truncated': False}],
              'source_evidence': [{'id': 'source-1', 'text': 'The meeting is Friday at 7pm. Lee declined the invitation.', 'truncated': False}],
              'source_scope': 'partial: expected-claim citations only', 'missing_source_ids': [],
              'answer_visible_scope': 'captured prefetch and direct tool results only; background traces excluded',
              'per_item_character_limit': 16000}
    output.mkdir(parents=True, exist_ok=True)
    cases = [('correct', 'The meeting is Friday at 7pm.', 'correct'),
             ('wrong_and_extra', 'The meeting is Saturday at 7pm. Lee is attending, and a dog will be there.', 'misrepresented')]
    results = []
    for name, answer, verdict in cases:
        grade = grade_semantic(name, 'When is the meeting?', answer, (claim,), judge,
                               record_path=output / f'{name}.json', diagnostic_inputs=inputs, diagnostic_version=DIAGNOSTIC_VERSION)
        diagnostic = grade.evidence_diagnostic
        statuses = [x['status'] for x in diagnostic.get('extra_assertions', [])]
        passed = (grade.slots[0].verdict == verdict and diagnostic['state'] == 'complete' and
                  diagnostic['claims'][0]['status'] == 'supported' and
                  (name != 'wrong_and_extra' or 'contradicted' in statuses and 'unverifiable' in statuses))
        results.append({'case': name, 'passed': passed, 'claim_verdict': grade.slots[0].verdict,
                        'evidence_diagnostic': diagnostic})
    # Exact excerpt from the recorded Memcal coffee prefetch, including the
    # citation marker that the v1 judge normalized incorrectly.
    unicode_text = '〔E2〕 Sun Nov 1  "Coffee", 9am — confirmed · with Riley'
    unicode_claim = Claim('coffee-time', 'Coffee is Sunday November 1 at 9am.', ('source-coffee',))
    unicode_inputs = {'answer_visible': [{'id': 'prefetch', 'text': unicode_text, 'truncated': False}],
        'source_evidence': [{'id': 'source-coffee', 'text': 'Coffee is Sunday November 1 at 9am.', 'truncated': False}],
        'source_scope': 'partial: expected-claim citations only', 'missing_source_ids': [],
        'answer_visible_scope': 'captured prefetch and direct tool results only; background traces excluded',
        'per_item_character_limit': 16000}
    unicode_answer = 'Coffee is Sunday November 1 at 9am.'
    grade = grade_semantic('unicode', 'When is coffee?', unicode_answer, (unicode_claim,), judge,
        record_path=output / 'unicode.json', diagnostic_inputs=unicode_inputs, diagnostic_version=DIAGNOSTIC_VERSION)
    bad = {'claims': [{'id': 'coffee-time', 'status': 'supported', 'reason': 'Altered marker',
        'citations': [{'id': 'prefetch', 'quote': unicode_text.replace('〔E2〕', '〈E2〉')}]}], 'extra_assertions': []}
    rejected = validate_diagnostic(bad, (unicode_claim,), unicode_answer, unicode_inputs, DIAGNOSTIC_VERSION)['state'] == 'ungraded'
    diagnostic = grade.evidence_diagnostic
    passed = (grade.slots[0].verdict == 'correct' and diagnostic['state'] == 'complete' and
              diagnostic['claims'][0]['status'] == 'supported' and rejected)
    results.append({'case': 'unicode', 'passed': passed, 'claim_verdict': grade.slots[0].verdict,
                    'altered_marker_rejected': rejected, 'evidence_diagnostic': diagnostic})
    return results
