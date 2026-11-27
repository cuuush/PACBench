from hashlib import sha256

from pacbench.providers import supermemory_evidence as week

def test_final_exact_answer_is_independent_of_a_missing_terminal_marker():
    response = dict(http_status=200, finished='now', response_validation=dict(finish_reasons=['stop'], envelope_errors=[],
        wire_parse_errors=0, assistant_text_sha256=sha256(b'Synthetic answer.').hexdigest(), sse_done_observed=False))
    assert week.verified_final_response(response, 'Synthetic answer.')
    assert not week.verified_final_response(response, 'Different answer.')
