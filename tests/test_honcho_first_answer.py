def test_stream_terminal_requires_a_complete_unique_done_frame():
    from pacbench.receipt_costs import read_frames

    assert read_frames(b'data: {}\n\ndata: [DONE]\n\n').sse_done_observed
    assert not read_frames(b'data: {}\n\ndata: [DONE]\n').sse_done_observed
    assert not read_frames(b'data: [DONE]\n\ndata: {}\n\n').sse_done_observed
