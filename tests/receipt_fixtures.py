"""Portable projections of reviewed public receipts, never private ledger reconstruction."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'audits/receipt-cost-observability-20261116'


def reviewed_receipts():
    """Preserve recorded receipt values when historical runner inputs are unavailable."""
    records = json.loads((EVIDENCE / 'retained-cost-report.json').read_text())['requests']
    return [{**{k: record.get(k) for k in ('id', 'project', 'model', 'started', 'finished',
               'state', 'http_status', 'error_label', 'input_tokens', 'output_tokens')},
             'cost_receipts': record['cost_receipts'],
             'receipt_generation_ids': record['generation_ids'],
             'served_provider': record['upstream_provider'],
             'accounted_upper_bound_usd': record['conservative_accounted_usd']}
            for record in records]
