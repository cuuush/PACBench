"""Persistent accounting identity for the separately authorized Pizza Day task."""
import json
from decimal import Decimal
from pathlib import Path

from .openrouter_route import MODEL, ENDPOINT

STATE = Path('/workspace/shared/pacbench-openrouter-pizza-day-20261112')


def require_authorization(budget):
    """A run cannot replace the task ledger or increase the approved total."""
    try:
        auth = json.loads((STATE / 'authorization.json').read_text())
    except (OSError, ValueError):
        raise RuntimeError('OpenRouter task spending authorization is unavailable') from None
    if (auth.get('task') != 'pacbench-pizza-day-20261112' or
            not ((auth.get('model')==MODEL and auth.get('endpoint')==ENDPOINT) or
                any(r.get('model')==MODEL and r.get('endpoint')==ENDPOINT for r in auth.get('additional_routes',[]))) or
            auth.get('approved_total_usd') != '20' or
            budget.path.resolve() != (STATE / 'ledger.json').resolve() or
            Decimal(budget.config['cap']) != Decimal('20')):
        raise RuntimeError('OpenRouter task accounting identity or spending limit differs')
    if getattr(budget, '_sending', False) and getattr(budget, '_created_this_request', True):
        raise RuntimeError('Task ledger must already exist; refusing to replace missing spending history')
    if not budget.path.is_file() or budget.path.stat().st_size == 0:
        raise RuntimeError('Task ledger is missing or empty; refusing to replace spending history')
    return auth
