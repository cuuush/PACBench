"""Read-only bridge from hash-bound direct receipts to main's cost reporting."""
import hashlib
import json
from pathlib import Path

from .receipt_costs import retained_metadata, safe_response_metadata


def direct_evidence(ledger, row):
    ledger = Path(ledger)
    result = retained_metadata(ledger, row)
    errors = []
    evidence = row.get('billing_receipt')
    if evidence:
        try:
            path = ledger.parent / evidence['relative_path']
            if (path.is_symlink() or not path.resolve().is_relative_to(ledger.parent.resolve())):
                raise ValueError('Receipt path outside ledger evidence')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != evidence['sha256']:
                raise ValueError('Receipt hash mismatch')
            receipt = json.loads(raw)
            if (receipt['protocol'] != 'openrouter-response-billing-v1' or
                    receipt['attempt_id'] != row['id'] or receipt['http_status'] != 200 or
                    receipt['cost_source'] != 'response.usage.cost'):
                raise ValueError('Receipt identity/source mismatch')
            metadata = safe_response_metadata({'id': receipt.get('generation_id'),
                'usage': receipt.get('usage')})
            for item in metadata['cost_receipts']:
                if item not in result['cost_receipts']:
                    result['cost_receipts'].append(item)
            for identifier in metadata['generation_ids']:
                if identifier not in result['receipt_generation_ids']:
                    result['receipt_generation_ids'].append(identifier)
            result['direct_receipt_body_complete'] = receipt['body_complete']
            result['served_provider'] = receipt.get('provider')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(type(exc).__name__)
    if row.get('receipt_metadata_error'):
        errors.append(row['receipt_metadata_error'])
    if errors:
        result['receipt_metadata_incomplete'] = True
        result['direct_receipt_evidence_errors'] = errors
    return result
