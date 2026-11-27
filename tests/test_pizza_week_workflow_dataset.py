"""Verify the frozen reconstruction against the archived v2 hashes."""
import hashlib
import json
from dataclasses import asdict

from pacbench.corpus.r2 import ROOT, compile_week


def export_hash(value):
    payload = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    return hashlib.sha256(payload.encode()).hexdigest()


def test_frozen_workflow_v2_reproduces_both_schedules():
    manifest = json.loads((ROOT / 'datasets/pizza-week/r2/workflow-v2.json').read_bytes())
    spec_path = ROOT / manifest['spec']
    assert hashlib.sha256(spec_path.read_bytes()).hexdigest() == manifest['spec_sha256']
    for state, expected in manifest['schedules'].items():
        _, documents, turns, frozen = compile_week(state, spec_path)
        assert frozen['sha256'] == manifest['source_contract_sha256']
        assert frozen['messages'] == manifest['source_records']
        assert len({sid for doc in documents for sid in doc.source_ids}) == manifest['source_records']
        assert len(documents) == manifest['nightly_documents']
        assert len({doc.cutoff for doc in documents}) == manifest['nightly_releases']
        index = [
            {'name': doc.filename, 'cutoff': doc.cutoff, 'hash': doc.sha256, 'ids': doc.source_ids}
            for doc in documents
        ]
        assert export_hash(index) == manifest['nightly_index_sha256']
        assert len(turns) == expected['turns']
        assert sum(len(turn.claims) for turn in turns) == expected['criteria']
        assert export_hash([asdict(turn) for turn in turns]) == expected['sha256']
