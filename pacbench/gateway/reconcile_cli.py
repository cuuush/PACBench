"""Run an explicit reconciliation pass against an existing private ledger."""
import argparse
import json
import os
from pathlib import Path

from .ledger import Ledger
from .openrouter_reconcile import fetch_generation, retry_due


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='Existing gateway state directory')
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        parser.error('OPENROUTER_API_KEY is required')
    try:
        ledger = Ledger(args.state/'ledger.sqlite3', existing_only=True)
        result = retry_due(ledger, lambda generation_id: fetch_generation(generation_id,key), limit=args.limit)
    except (OSError, ValueError, __import__('sqlite3').Error):
        parser.error('Existing gateway ledger unavailable or invalid')
    counts = {}
    for value in result.values(): counts[value] = counts.get(value,0)+1
    print(json.dumps({'results':counts},sort_keys=True))


if __name__ == '__main__':
    main()
