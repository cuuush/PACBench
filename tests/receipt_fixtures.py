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


class JournalFixture:
    """Write synthetic rows with the real projection and replay its authority read-only."""
    strict = False
    def __init__(self, path):
        self.path = path

    def journal(self):
        from pacbench.receipt_projection import Projection
        return Projection(self.path).open(initialize=not self.strict, strict=self.strict)

    def snapshot(self):
        from pacbench.receipt_costs import read_attempts
        return read_attempts(self.path)

    def get_attempt(self, ident):
        with self.journal() as (rows, _):
            return rows[ident]

    def has_active_attempts(self):
        with self.journal() as (rows, _):
            return rows.active()


class StrictJournalFixture(JournalFixture):
    strict = True
    def initialize_from_journal(self):
        from pacbench.receipt_projection import Projection
        with Projection(self.path).open(initialize=True):
            pass
