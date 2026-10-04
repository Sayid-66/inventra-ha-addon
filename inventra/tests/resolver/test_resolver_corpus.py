"""Offline baseline of fetch -> merge; accuracy failures are intentional."""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path

import httpx
import pytest

from inventra_backend.resolver.quantity import parse_quantity
from inventra_backend.resolver.resolver_service import _merge_all_fields
from inventra_backend.resolver.sources import ALL_SOURCES, build_client
from .corpus_products import CORPUS

ACCURACY_GATE_ENABLED = True
FIELDS = ("name", "brand", "quantity", "category", "variant", "imageUrl")
GROUPS = (
    "fleisch_fisch", "wurst_aufschnitt", "milch_kaese_eier", "obst_gemuese",
    "backwaren_nudeln_reis", "konserven_saucen_oele", "snacks_suesswaren",
    "getraenke_alkoholfrei", "getraenke_alkohol", "tiefkuehl",
    "haushalt_reinigung", "drogerie_kosmetik", "tiernahrung",
    "baby_bio_sonstiges", "edge_cases",
)


def _quantity_key(value):
    parsed = parse_quantity(value)
    return (parsed.amount, parsed.unit, parsed.pack_count) if parsed else None


def _equal(field, expected, actual):
    if field != "quantity":
        return expected == actual
    if expected is None or actual is None:
        return expected is actual
    # An unparseable actual string must not receive credit for missing data.
    target = _quantity_key(expected)
    return target is not None and target == _quantity_key(actual)


async def _evaluate():
    correct = Counter({field: 0 for field in FIELDS})
    failures, errors = [], []
    all_correct = completed = 0
    configs = {config.source_id: config for config in ALL_SOURCES}
    for case in CORPUS:
        try:
            results = {}
            for source, payload in case["sources"].items():
                def handler(request, payload=payload):
                    assert request.url.path == f"/api/v2/product/{case['barcode']}.json"
                    return httpx.Response(200, json=payload)

                client = build_client(configs[source], 1.0, "Inventra/corpus-test",
                                      transport=httpx.MockTransport(handler))
                result = await client.fetch(case["barcode"])
                assert result.status != "ERROR", result.error
                results[source] = result
            merged = _merge_all_fields(results)
            assert set(merged) == set(FIELDS)
            completed += 1
            product_ok = True
            raw = {source: {key: payload.get("product", {}).get(key) for key in (
                "product_name_de", "product_name", "brands", "quantity",
                "product_quantity", "product_quantity_unit",
            )} for source, payload in case["sources"].items()}
            for field in FIELDS:
                expected, actual = case["expected"][field], merged[field].value
                if _equal(field, expected, actual):
                    correct[field] += 1
                else:
                    product_ok = False
                    failures.append({"id": case["id"], "field": field,
                                     "expected": expected, "actual": actual,
                                     "raw_sources": raw})
            all_correct += int(product_ok)
        except Exception as exc:
            errors.append({"id": case["id"], "error": repr(exc)})
    total = len(CORPUS)
    return {"total": total, "completed": completed,
            "fields": {field: {"correct": correct[field], "total": total,
                               "accuracy": correct[field] / total}
                       for field in FIELDS},
            "all_fields_correct": {"correct": all_correct, "total": total,
                                   "accuracy": all_correct / total},
            "failures": failures, "errors": errors}


@pytest.fixture(scope="module")
def scorecard():
    async def bounded():
        return await asyncio.wait_for(_evaluate(), timeout=30)

    return asyncio.run(bounded())


def test_corpus_sanity():
    assert len(CORPUS) >= 160
    assert len({case["id"] for case in CORPUS}) == len(CORPUS)
    assert len({case["barcode"] for case in CORPUS}) == len(CORPUS)
    counts = Counter(case["category_group"] for case in CORPUS)
    assert set(counts) == set(GROUPS)
    assert all(counts[group] >= 8 for group in GROUPS), counts
    coverage = Counter(pattern for case in CORPUS for pattern in case["patterns"])
    assert all(coverage[f"P{i}"] >= 6 for i in range(1, 21)), coverage
    assert coverage["P4"] >= 8
    for case in CORPUS:
        assert len(case["barcode"]) == 13 and case["barcode"].isascii()
        assert case["barcode"].isdigit()
        assert set(case["expected"]) == set(FIELDS)
        assert case["expected"]["variant"] is None
        assert set(case["sources"]) <= {c.source_id for c in ALL_SOURCES}
        quantity = case["expected"]["quantity"]
        assert quantity is None or _quantity_key(quantity) is not None
        for payload in case["sources"].values():
            for key in ("product_name_de", "product_name", "brands"):
                value = payload.get("product", {}).get(key, "")
                assert "?" not in value or "P17" in case["patterns"]


def test_corpus_scorecard(scorecard):
    print("\nResolver corpus baseline")
    print(f"{'Field':<20} {'Correct / total':>17} {'Accuracy':>10}")
    for field, result in (*scorecard["fields"].items(),
                          ("ALL fields", scorecard["all_fields_correct"])):
        fraction = f"{result['correct']} / {result['total']}"
        print(f"{field:<20} {fraction:>17} {result['accuracy']:>9.2%}")
    report_path = os.environ.get("INVENTRA_CORPUS_REPORT")
    if report_path:
        Path(report_path).write_text(json.dumps(scorecard, ensure_ascii=False, indent=2)
                                    + "\n", encoding="utf-8")
    assert scorecard["fields"]
    assert not scorecard["errors"], scorecard["errors"]
    assert scorecard["completed"] == len(CORPUS)


@pytest.mark.skipif(not ACCURACY_GATE_ENABLED, reason="Corpus accuracy gate disabled")
def test_corpus_accuracy_gate(scorecard):
    assert not scorecard["errors"], scorecard["errors"]
    thresholds = {"name": .95, "brand": .95, "quantity": .95,
                  "category": .95, "variant": 1.0}
    missed = {field: scorecard["fields"][field]["accuracy"]
              for field, threshold in thresholds.items()
              if scorecard["fields"][field]["accuracy"] < threshold}
    if scorecard["all_fields_correct"]["accuracy"] < .90:
        missed["ALL fields"] = scorecard["all_fields_correct"]["accuracy"]
    assert not missed, f"Target accuracy thresholds missed: {missed}"


def test_corpus_expected_names_are_idempotent():
    from inventra_backend.resolver.product_naming import compose_product_name, split_brands
    failures = []
    for case in CORPUS:
        expected = case["expected"]["name"]
        if expected is None:
            continue
        for source, payload in case["sources"].items():
            brands = split_brands(payload.get("product", {}).get("brands"))
            actual = compose_product_name(expected, brands)
            if actual != expected:
                failures.append((case["id"], source, expected, actual))
    assert not failures, failures
