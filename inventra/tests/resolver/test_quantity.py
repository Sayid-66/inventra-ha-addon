from inventra_backend.resolver.quantity import QuantityCandidate, parse_quantity


def test_parses_simple_ml():
    q = parse_quantity("500 ml")
    assert q == QuantityCandidate(amount=500.0, unit="ml", pack_count=1, raw_text="500 ml")


def test_parses_simple_l_with_comma():
    q = parse_quantity("0,5 l")
    assert q == QuantityCandidate(amount=0.5, unit="l", pack_count=1, raw_text="0,5 l")


def test_parses_multipack_preserving_structure():
    q = parse_quantity("6 x 1,5 l")
    assert q == QuantityCandidate(amount=1.5, unit="l", pack_count=6, raw_text="6 x 1,5 l")


def test_parses_multipack_with_multiplication_sign_and_no_spaces():
    q = parse_quantity("6x1.5L")
    assert q == QuantityCandidate(amount=1.5, unit="l", pack_count=6, raw_text="6x1.5L")


def test_returns_none_for_unparseable_text():
    assert parse_quantity("irgendein Text ohne Menge") is None


def test_returns_none_for_empty_text():
    assert parse_quantity("") is None
    assert parse_quantity(None) is None


def test_500ml_and_half_liter_equivalent_for_comparison_only():
    a = parse_quantity("500 ml")
    b = parse_quantity("0,5 l")
    assert a.total_base_unit_amount() == b.total_base_unit_amount()
    # but the literal parsed value is never rewritten into the other's spelling:
    assert a.unit == "ml" and b.unit == "l"


def test_multipack_total_is_pack_count_times_per_unit_amount():
    q = parse_quantity("6 x 1,5 l")
    assert q.total_base_unit_amount() == 9000.0  # 9 l expressed in ml (base unit for volume)


def test_single_pack_total_equals_amount_in_base_unit():
    q = parse_quantity("500 ml")
    assert q.total_base_unit_amount() == 500.0
