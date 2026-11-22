from pathlib import Path

import pytest


@pytest.fixture
def historical_checkpoint_archive():
    """Only the exact historical R2 audit can reconstruct these native checkpoint permits."""
    runs = Path(__file__).resolve().parents[1]/'runs'
    if not any((runs/name).exists() for name in (
            'pizza-week-evaluator-reuse-offline-20261115','pizza-week-revised-20261115')):
        pytest.skip('requires the original R2 preparation and preflight checksums')
