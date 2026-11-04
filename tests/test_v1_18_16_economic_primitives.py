"""c2 section 3 scalar preparation, not new-pack/Backend integration evidence."""
from decimal import localcontext

import pytest

from mayim_agent._contract_schema import (
    validate_atomic_amount, validate_economic_revision, validate_floor_amount,
    validate_plan_label, validate_safe_count,
)


def test_unlisted_conditions_do_not_need_price_or_revision_registration():
    # Two different snapshots and an unknown pure label use identical code.
    for revision, amount, capacity, label in (
        ('conditions-A', '25000', 3, 'C3'),
        ('future.opaque~B_2', '37123', 17, 'Product-with-no-numeric-capacity'),
    ):
        assert validate_economic_revision(revision) == revision
        assert validate_atomic_amount(amount) == amount
        assert validate_safe_count(capacity) == capacity
        assert validate_plan_label(label, maximum=128) == label
    assert validate_economic_revision('x' * 128) == 'x' * 128


@pytest.mark.parametrize('value', [None, '', 'x' * 129, 'rev\n', 'a/b', '条件', 1, True])
def test_revision_is_required_and_ascii_bounded(value):
    with pytest.raises(ValueError):
        validate_economic_revision(value)


@pytest.mark.parametrize('value', [None, '', 0, 1, 1.5, True, '-1', '+1', '01',
    '1.0', '1e3', '１', '1\n', str(2 ** 256), '9' * 79])
def test_atomic_amount_rejects_coercion_and_overflow(value):
    with pytest.raises(ValueError):
        validate_atomic_amount(value, allow_zero=True)


def test_zero_is_explicit_and_large_atomic_amounts_remain_strings():
    with pytest.raises(ValueError):
        validate_atomic_amount('0')
    assert validate_atomic_amount('0', allow_zero=True) == '0'
    assert validate_atomic_amount(str(2 ** 256 - 1)) == str(2 ** 256 - 1)
    assert validate_atomic_amount('9007199254740993') == '9007199254740993'


@pytest.mark.parametrize('value', [None, '3', True, 3.0, -1, 2 ** 53])
def test_capacity_rejects_nonintegers_and_unsafe_counts(value):
    with pytest.raises(ValueError):
        validate_safe_count(value, allow_zero=True)


def test_capacity_does_not_infer_a_plan_or_claim_server_admission():
    assert validate_safe_count(2 ** 53 - 1) == 2 ** 53 - 1
    assert validate_safe_count(0, allow_zero=True) == 0
    with pytest.raises(ValueError):
        validate_safe_count(0)


@pytest.mark.parametrize('value', [None, '', 3, 'x' * 129])
def test_plan_requires_its_received_bound(value):
    with pytest.raises(ValueError):
        validate_plan_label(value, maximum=128)


def test_floor_checks_saved_basis_without_decimal_context_rounding():
    # Expected values are independent exact arithmetic, not a product helper.
    base = 2 ** 256 - 1
    expected = str(base * 37 // 100)
    with localcontext() as context:
        context.prec = 6
        assert validate_floor_amount(str(base), '0.37', expected) == expected
    assert validate_floor_amount('25001', '0.333', '8325') == '8325'
    assert validate_floor_amount('25000', '1', '25000') == '25000'
    assert validate_floor_amount('25000', '0', '0') == '0'
    with pytest.raises(ValueError):
        validate_floor_amount('37123', '1', '25000')
    with pytest.raises(ValueError):
        validate_floor_amount('25001', '0.333', '8326')


@pytest.mark.parametrize('coefficient', [None, 0.7, '1.1', '-0.1', '0.70', 'NaN'])
def test_coefficient_must_be_lossless_and_valid(coefficient):
    with pytest.raises(ValueError):
        validate_floor_amount('25000', coefficient, '17500')
