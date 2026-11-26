"""Bind a source-only Supermemory inference to one durable native update.

This is an admission check for the existing observational receipt journal. It
does not register a provider scope or grant authority to start a native service.
"""
from dataclasses import dataclass
import re

from ...dispatch_ownership import OwnershipConflict

BOUNDARY = 'supermemory-stock-source-durable-update-v1'
NATIVE_ID = re.compile(r'[A-Za-z0-9_-]{22}')


@dataclass(frozen=True)
class SourceEffect:
    update_id: str
    document_id: str

    @classmethod
    def from_native(cls, witness, released_documents):
        if not isinstance(witness, dict) or set(witness) != {'updateId', 'documentId'}:
            raise OwnershipConflict('No uniquely attributed stock Supermemory document update')
        update, document = witness['updateId'], witness['documentId']
        if not all(isinstance(value, str) and NATIVE_ID.fullmatch(value) for value in (update, document)):
            raise OwnershipConflict('Invalid stock Supermemory document update identity')
        if document not in released_documents:
            raise OwnershipConflict('Supermemory worker update refers to a document outside the admitted release')
        return cls(update, document)


def check_update_history(rows, effect):
    """Call under the receipt lock before preparing a new physical send."""
    for row in rows:
        prior = row.get('workflow_slot', {}).get('stock_source_effect')
        if not isinstance(prior, dict):
            raise OwnershipConflict('Foreign receipt in the Supermemory source qualifier')
        if prior['update_id'] == effect.update_id:
            # Body digests are evidence. An upstream request with changed bytes
            # still belongs to the same durable work and cannot authorize itself.
            raise OwnershipConflict('Supermemory durable update already prepared; no automatic replay')
        if row.get('finished') and (row.get('dispatch_execution') != 'response_ended'
                                    or row.get('http_status') != 200):
            raise OwnershipConflict('Previous Supermemory source transport did not reach an unambiguous end')
