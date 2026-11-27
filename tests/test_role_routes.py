"""Mixed routes exercise real reservations and client hooks against fake responses."""

from decimal import Decimal

def test_resumed_child_envelope_does_not_double_count_spent_or_unknown():
    from pacbench.receipt_costs import liability_envelope
    calls=[{'accounted':'12','state':'uncertain'}, {'cohort_id':'unit','accounted':'.1','state':'settled'},
           {'cohort_id':'unit','accounted':'.5','state':'uncertain'}]
    r=liability_envelope(calls,'8','unit')
    assert r['funded'] and Decimal(r['remaining_envelope_usd'])==Decimal('7.4')
    assert Decimal(r['available_usd'])==Decimal('7.4')
    assert not liability_envelope(calls,'8','new-child-id')['funded']
