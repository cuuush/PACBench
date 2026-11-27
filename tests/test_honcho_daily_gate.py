from pacbench.providers.honcho_evidence import canonical_inputs


def test_first_release_preserves_source_order_and_manifest():
    documents, manifest = canonical_inputs(first_release=True)
    full, parent = canonical_inputs()
    assert documents == full[:1] and len(documents[0].source_ids) == 39
    assert manifest['document_index'] == parent['document_index'][:1]
    assert manifest['parent_index_sha256'] == parent['normalized_index_sha256']
