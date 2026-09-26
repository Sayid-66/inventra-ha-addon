from inventra_backend.resolver.scoring import Confidence, merge_text_field, merge_quantity_field


def test_single_plausible_source_is_at_most_medium():
    # One plausible source alone, with no consensus, must not reach HIGH —
    # consensus is what lifts an already-plausible value (spec §6).
    result = merge_text_field("name", {"off": "Balea Handcreme"})
    assert result.value == "Balea Handcreme"
    assert result.confidence in (Confidence.MEDIUM, Confidence.LOW)
    assert result.selected_source == "off"
    assert result.contributing_sources == ["off"]
    assert result.conflicting_sources == []


def test_agreement_across_sources_raises_confidence_and_lists_contributors():
    result = merge_text_field("name", {"off": "Balea Handcreme", "opf": "balea handcreme"})
    assert result.confidence == Confidence.HIGH
    assert set(result.contributing_sources) == {"off", "opf"}
    assert result.conflicting_sources == []


def test_disagreement_is_recorded_as_conflict_not_silently_dropped():
    result = merge_text_field("name", {"off": "Balea Handcreme", "opf": "Nivea Handcreme"})
    assert result.selected_source in ("off", "opf")
    assert result.conflicting_sources != []
    assert result.value is not None  # the chosen (still plausible) candidate remains usable


def test_gate_failed_candidate_never_becomes_value_or_suggestion():
    garbage = "Pantothensure ZERO GO NRGY by 9180 BOOST BERRIES G"
    result = merge_text_field("name", {"off": garbage})
    assert result.value is None
    assert result.suggested is None
    assert result.confidence is None


def test_url_kind_uses_the_dedicated_image_url_gate_not_generic_text_gate():
    assert merge_text_field(
        "url", {"off": "https://images.example.org/p.jpg"},
    ).value == "https://images.example.org/p.jpg"
    assert merge_text_field("url", {"off": "not a url at all"}).value is None


def test_all_sources_missing_field_yields_empty_result():
    result = merge_text_field("name", {"off": None, "opf": None})
    assert result.value is None
    assert result.suggested is None


def test_consensus_bonus_is_capped_not_four_independent_proofs():
    # All four Open-Facts sources share one codebase (spec §6 item 2) — full
    # four-way agreement on an otherwise unremarkable value still must not
    # be treated as stronger evidence than plausibility + a couple of
    # agreeing sources already provides.
    two_source = merge_text_field("name", {"off": "Milch", "opf": "Milch"})
    four_source = merge_text_field(
        "name", {"off": "Milch", "obf": "Milch", "opff": "Milch", "opf": "Milch"},
    )
    assert two_source.confidence == Confidence.HIGH
    assert four_source.confidence == Confidence.HIGH  # capped, not "extra high"


def test_no_fabricated_spelling_stored_value_is_a_real_source_text():
    result = merge_text_field("name", {"off": "GO NRGY", "opf": "GO NRGY"})
    assert result.value == "GO NRGY"  # never rewritten to "Go Nrgy" or similar


def test_quantity_merge_is_atomic_never_mixes_amount_and_unit_across_sources():
    result = merge_quantity_field({"off": "500 ml", "opf": "500 g"})
    # both plausible but conflicting QuantityCandidates -> one whole
    # candidate wins, never "500" from one + unit from the other.
    assert result.value in ("500 ml", "500 g")
    assert result.conflicting_sources != []


def test_quantity_merge_recognizes_equivalent_spellings_as_consensus():
    result = merge_quantity_field({"off": "500 ml", "opf": "0,5 l"})
    assert result.confidence == Confidence.HIGH
    assert set(result.contributing_sources) == {"off", "opf"}
    assert result.value in ("500 ml", "0,5 l")  # literal source text, not a synthesized spelling


def test_quantity_merge_preserves_multipack_structure():
    result = merge_quantity_field({"off": "6 x 1,5 l"})
    assert result.value == "6 x 1,5 l"  # never collapsed to "1,5 l" or "9 l"
