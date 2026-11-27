"""Verify the chunk read boundary before any paid Supermemory continuation."""

from hashlib import sha256

from types import SimpleNamespace

import pytest

from pacbench.providers import supermemory_evidence as chunks

from pacbench.workflows.progress import OwnershipConflict

DOC, UPDATE = 'd' * 22, 'u' * 22

def anchor(readback, revision, publication, at_ms=1):
    return dict(native_id=DOC, namespace='week', session_id='hermes-warm', revision=revision,
                readback=dict(chunk_anchor=dict(protocol=chunks.CHUNK_PROTOCOL, at_ms=at_ms, revision=revision,
                                                publication=publication, chunks=readback)))

def test_anchor_checks_one_stable_published_chunk_inventory():
    stamp = '2026-10-05T21:00:00-04:00'
    v3 = dict(id=DOC, customId='chat', containerTags=['week'], latestRevision=5, activeContentUpdateId='p' * 22,
              status='done', createdAt=stamp, updatedAt=stamp, content='message', metadata={})
    v5 = dict(content='message', metadata={}, system=dict(status='done', createdAt=stamp, updatedAt=stamp),
              chunks=[dict(id='c' * 22, position=0, content='message')])
    document = SimpleNamespace(filename='chat')
    proof = chunks.capture_anchor(v3, v5, v3, document, native_id=DOC, namespace='week')
    assert proof['revision'] == 5 and proof['chunks'][0]['sha256'] == sha256(b'message').hexdigest()
    for changed in (dict(v3, activeContentUpdateId='s' * 22), dict(v3, latestRevision=6)):
        with pytest.raises(OwnershipConflict, match='publication changed'):
            chunks.capture_anchor(v3, v5, changed, document, native_id=DOC, namespace='week')

def test_identical_observed_chunks_report_both_eligible_revisions():
    observed = dict(at_ms=5, chunks=[dict(id='a' * 22, position=0, sha256='a' * 64)])
    candidates = [dict(anchor(observed['chunks'], rev, chr(64 + rev) * 22, at_ms=rev), question_id=f'q{rev}')
                  for rev in (4, 5, 6)]
    proof = chunks._candidates(observed, candidates, opening=True)
    assert [row['revision'] for row in proof['candidates']] == [4, 5]

@pytest.mark.parametrize('returned,positions', [(1, [0, 4]), (0, [4]), (1, [])])
def test_readchunks_omitted_requested_row_cannot_match_revision(returned, positions):
    rows = [dict(id=chr(97 + i) * 22, position=i, sha256='a' * 64) for i in (0, 4)]
    anchors = [dict(anchor(rows, 4, 'p' * 22), question_id='q4')]
    observed = dict(at_ms=3, chunks=rows[:returned], arguments={'positions': positions}, result_sha256='b' * 64)
    with pytest.raises(OwnershipConflict, match='eligible verified capture'):
        chunks._candidates(observed, anchors, opening=False)
