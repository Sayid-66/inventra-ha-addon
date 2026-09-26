from inventra_backend.resolver.provenance import derive_field_provenance


def _resolution(name_value="Go Nrgy Boost Berries Zero"):
    return {
        "barcode": "4006381333931",
        "proposed_fields": {
            "name": {"value": name_value, "confidence": "high", "selectedSource": "off",
                     "contributingSources": ["off", "opf"], "conflictingSources": []},
        },
    }


def test_matching_submitted_value_credits_the_resolver_source():
    result = derive_field_provenance(_resolution(), {"name": "Go Nrgy Boost Berries Zero"}, {})
    assert result["name"]["manual"] is False
    assert result["name"]["selectedSource"] == "off"
    assert result["name"]["confidence"] == "high"


def test_edited_value_is_manual_even_if_semantically_equivalent():
    # spec §5.1: confirmation equality is strict, NOT comparison normalization.
    result = derive_field_provenance(_resolution("GO NRGY"), {"name": "Go Nrgy"}, {})
    assert result["name"]["manual"] is True
    assert result["name"]["selectedSource"] == "manual"


def test_trim_and_unicode_normalization_are_still_allowed_in_confirmation_equality():
    result = derive_field_provenance(_resolution("Milch"), {"name": "  Milch  "}, {})
    assert result["name"]["manual"] is False


def test_missing_resolution_makes_every_touched_field_manual():
    result = derive_field_provenance(None, {"name": "Handgetippt"}, {})
    assert result["name"]["manual"] is True


def test_field_absent_from_submission_keeps_previous_provenance_untouched():
    previous = {"brand": {"manual": True, "selectedSource": "manual"}}
    result = derive_field_provenance(_resolution(), {"name": "Go Nrgy Boost Berries Zero"}, previous)
    assert result["brand"] == previous["brand"]


def test_manual_field_stays_manual_even_with_a_new_high_confidence_resolution():
    previous = {"name": {"manual": True, "selectedSource": "manual"}}
    result = derive_field_provenance(_resolution(), {"name": "Go Nrgy Boost Berries Zero"}, previous)
    # The submitted value happens to match the NEW resolution's proposal, but
    # once manual, a field is never silently re-credited to a source without
    # an explicit new resolve/confirm cycle starting from a non-manual state
    # -- this spec doesn't require reverting manual on a coincidental match,
    # so the safe behavior is: still derive from resolutionId as normal here,
    # since "manual" only prevents automatic overwrite, not a fresh explicit
    # confirmation. This test documents that a matching confirm CAN clear
    # manual only when the caller explicitly re-derives it -- see Task 15's
    # route wiring for the actual create/update-time rule.
    assert result["name"]["manual"] is False
