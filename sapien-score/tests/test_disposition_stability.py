# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for Module 4 DISPOSITION STABILITY (rescoped Axis 5).

Hand-constructed variant-outcome sets with KNOWN stability, exact assertions on
modal-agreement (SCR), normalized stance entropy, per-family flip rates, and the
DSI bundle. The variant-generation seam (plan_variant_conditions) is exercised
with injected string/callable paraphrasers, seeds, and system prompts — no LLM
in the loop. Zero-denominator and reference-inference edge cases are covered.
"""
import sys
from math import log
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.hallucination.disposition_stability import (  # noqa: E402
    ANTI_SYCOPHANCY,
    PARAPHRASE,
    PERTURBATION_FAMILIES,
    REFERENCE,
    SEED,
    ScenarioDispositions,
    VariantCondition,
    VariantOutcome,
    condition_flip_rate,
    disposition_stability_index,
    plan_variant_conditions,
    stance_consistency_rate,
    stance_entropy,
)

# Shorthand stance labels.
TRUE = "TRUE"
FALSE = "FALSE"
EVADE = "EVADE"


def _vo(family, stance):
    return VariantOutcome(condition_family=family, stance=stance)


def _scenario(stances_by_family, reference_stance=None):
    """Build a ScenarioDispositions from {family: [stance, ...]}."""
    variants = []
    for fam, stances in stances_by_family.items():
        for s in stances:
            variants.append(_vo(fam, s))
    return ScenarioDispositions(
        variants=variants, reference_stance=reference_stance
    )


# ---------------------------------------------------------------------------
# Variant-generation seam (pure, injected). No LLM.
# ---------------------------------------------------------------------------
class TestPlanVariantConditions:
    def test_reference_included_by_default(self):
        plan = plan_variant_conditions("base")
        assert len(plan) == 1
        assert plan[0].condition_family == REFERENCE
        assert plan[0].pressure == "base"

    def test_reference_can_be_omitted(self):
        plan = plan_variant_conditions("base", include_reference=False)
        assert plan == []

    def test_literal_and_callable_paraphrases(self):
        plan = plan_variant_conditions(
            "base pressure",
            paraphrases=["reworded", lambda p: p.upper()],
            include_reference=False,
        )
        assert [c.condition_family for c in plan] == [PARAPHRASE, PARAPHRASE]
        assert plan[0].pressure == "reworded"
        assert plan[1].pressure == "BASE PRESSURE"  # callable applied to base
        assert plan[1].variant_id == f"{PARAPHRASE}:1"

    def test_seeds_and_system_prompts(self):
        plan = plan_variant_conditions(
            "base",
            seeds=[7, 9],
            system_prompts=[("strict", "Do not defer."), ("cite", "Cite.")],
            include_reference=False,
        )
        seeded = [c for c in plan if c.condition_family == SEED]
        antisyc = [c for c in plan if c.condition_family == ANTI_SYCOPHANCY]
        assert [c.seed for c in seeded] == [7, 9]
        assert all(c.pressure == "base" for c in seeded)
        assert antisyc[0].system_prompt == "Do not defer."
        assert antisyc[0].variant_id == f"{ANTI_SYCOPHANCY}:strict"

    def test_all_axes_one_at_a_time(self):
        plan = plan_variant_conditions(
            "base",
            paraphrases=["p1"],
            seeds=[1],
            system_prompts=[("s", "sys")],
        )
        fams = [c.condition_family for c in plan]
        assert fams == [REFERENCE, PARAPHRASE, SEED, ANTI_SYCOPHANCY]

    def test_bad_paraphrase_type_raises(self):
        with pytest.raises(ValueError):
            plan_variant_conditions("base", paraphrases=[123])

    def test_returns_variant_condition_instances(self):
        plan = plan_variant_conditions("base", seeds=[1])
        assert all(isinstance(c, VariantCondition) for c in plan)


# ---------------------------------------------------------------------------
# Stance Consistency Rate (SCR) — modal agreement, primary signal.
# ---------------------------------------------------------------------------
class TestStanceConsistencyRate:
    def test_perfectly_stable_is_one(self):
        scs = [_scenario({REFERENCE: [TRUE], PARAPHRASE: [TRUE, TRUE]})]
        r = stance_consistency_rate(scs)
        assert r["estimate"] == pytest.approx(1.0)
        assert r["n"] == 1

    def test_even_split_modal_agreement(self):
        # 2 TRUE, 2 FALSE -> modal agreement 0.5.
        scs = [_scenario({PARAPHRASE: [TRUE, TRUE, FALSE, FALSE]})]
        r = stance_consistency_rate(scs)
        assert r["estimate"] == pytest.approx(0.5)

    def test_mean_across_scenarios(self):
        # scenario A: all same -> 1.0 ; scenario B: 2/3 modal -> 0.6667
        a = _scenario({PARAPHRASE: [TRUE, TRUE, TRUE]})
        b = _scenario({PARAPHRASE: [TRUE, TRUE, FALSE]})
        r = stance_consistency_rate([a, b])
        assert r["estimate"] == pytest.approx((1.0 + 2 / 3) / 2)
        assert r["n"] == 2

    def test_family_filter(self):
        sc = _scenario(
            {REFERENCE: [TRUE], PARAPHRASE: [FALSE, FALSE], SEED: [TRUE, FALSE]}
        )
        # paraphrase family: both FALSE -> 1.0
        assert stance_consistency_rate([sc], family=PARAPHRASE)[
            "estimate"
        ] == pytest.approx(1.0)
        # seed family: split -> 0.5
        assert stance_consistency_rate([sc], family=SEED)[
            "estimate"
        ] == pytest.approx(0.5)

    def test_empty_scenario_skipped(self):
        # A scenario with no variants in the requested family contributes nothing
        sc = _scenario({REFERENCE: [TRUE]})
        r = stance_consistency_rate([sc], family=PARAPHRASE)
        assert r["estimate"] is None
        assert r["n"] == 0
        assert r["scenarios_total"] == 1

    def test_ci_present_for_multiple_scenarios(self):
        scs = [
            _scenario({PARAPHRASE: [TRUE, TRUE, TRUE]}),
            _scenario({PARAPHRASE: [TRUE, TRUE, FALSE]}),
            _scenario({PARAPHRASE: [TRUE, FALSE, FALSE]}),
        ]
        r = stance_consistency_rate(scs)
        lo, hi = r["ci"]
        assert lo is not None and hi is not None
        assert lo <= r["estimate"] <= hi


# ---------------------------------------------------------------------------
# Normalized stance entropy — instability signal.
# ---------------------------------------------------------------------------
class TestStanceEntropy:
    def test_all_same_is_zero(self):
        scs = [_scenario({PARAPHRASE: [TRUE, TRUE, TRUE]})]
        assert stance_entropy(scs)["mean"] == pytest.approx(0.0)

    def test_even_two_way_split_is_one(self):
        # 2 TRUE, 2 FALSE, K=2 observed -> H = log 2, normalized = 1.0
        scs = [_scenario({PARAPHRASE: [TRUE, TRUE, FALSE, FALSE]})]
        assert stance_entropy(scs)["mean"] == pytest.approx(1.0)

    def test_hand_computed_three_way(self):
        # counts 2 TRUE, 1 FALSE, 1 EVADE ; n=4 ; K=3 observed.
        # H = -(0.5 ln0.5 + 0.25 ln0.25 + 0.25 ln0.25); norm = H/ln3
        labels = [TRUE, TRUE, FALSE, EVADE]
        h = -(0.5 * log(0.5) + 0.25 * log(0.25) + 0.25 * log(0.25))
        expected = h / log(3)
        scs = [_scenario({PARAPHRASE: labels})]
        assert stance_entropy(scs)["mean"] == pytest.approx(expected)

    def test_fixed_categories_normalisation(self):
        # 2 TRUE, 2 FALSE but normalise by a 3-label alphabet -> H/ln3 < 1
        labels = [TRUE, TRUE, FALSE, FALSE]
        expected = (-(0.5 * log(0.5) + 0.5 * log(0.5))) / log(3)
        scs = [_scenario({PARAPHRASE: labels})]
        r = stance_entropy(scs, categories=[TRUE, FALSE, EVADE])
        assert r["mean"] == pytest.approx(expected)

    def test_empty_is_none(self):
        r = stance_entropy([_scenario({REFERENCE: [TRUE]})], family=PARAPHRASE)
        assert r["mean"] is None
        assert r["n"] == 0


# ---------------------------------------------------------------------------
# Condition flip rate — stance under a family vs the reference.
# ---------------------------------------------------------------------------
class TestConditionFlipRate:
    def test_flip_detected(self):
        # reference TRUE, paraphrase modal FALSE -> flip
        sc = _scenario({REFERENCE: [TRUE], PARAPHRASE: [FALSE, FALSE]})
        r = condition_flip_rate([sc], PARAPHRASE)
        assert r["estimate"] == pytest.approx(1.0)
        assert r["successes"] == 1
        assert r["n"] == 1
        assert r["family"] == PARAPHRASE

    def test_no_flip_when_modal_matches_reference(self):
        sc = _scenario({REFERENCE: [TRUE], PARAPHRASE: [TRUE, FALSE, TRUE]})
        r = condition_flip_rate([sc], PARAPHRASE)
        assert r["estimate"] == pytest.approx(0.0)

    def test_explicit_reference_stance_used(self):
        sc = _scenario({PARAPHRASE: [FALSE, FALSE]}, reference_stance=TRUE)
        assert condition_flip_rate([sc], PARAPHRASE)["estimate"] == 1.0

    def test_reference_inferred_from_all_when_no_reference_family(self):
        # No REFERENCE family, no explicit ref -> inferred modal over all = TRUE.
        # anti-sycophancy modal FALSE differs -> flip.
        sc = _scenario(
            {SEED: [TRUE, TRUE, TRUE], ANTI_SYCOPHANCY: [FALSE, FALSE]}
        )
        assert condition_flip_rate([sc], ANTI_SYCOPHANCY)["estimate"] == 1.0

    def test_scenarios_without_family_excluded(self):
        good = _scenario({REFERENCE: [TRUE], SEED: [FALSE]})
        skip = _scenario({REFERENCE: [TRUE]})  # no SEED variant
        r = condition_flip_rate([good, skip], SEED)
        assert r["n"] == 1

    def test_all_none_when_no_eligible(self):
        r = condition_flip_rate([_scenario({REFERENCE: [TRUE]})], SEED)
        assert r["estimate"] is None
        assert r["n"] == 0

    def test_bad_family_raises(self):
        with pytest.raises(ValueError):
            condition_flip_rate([], REFERENCE)


# ---------------------------------------------------------------------------
# DSI bundle.
# ---------------------------------------------------------------------------
class TestDispositionStabilityIndex:
    def test_bundle_shape_and_note(self):
        sc = _scenario(
            {
                REFERENCE: [TRUE],
                PARAPHRASE: [TRUE, TRUE],
                SEED: [TRUE, FALSE],
                ANTI_SYCOPHANCY: [FALSE, FALSE],
            }
        )
        dsi = disposition_stability_index([sc])
        assert set(dsi["by_family"].keys()) == set(PERTURBATION_FAMILIES)
        assert dsi["overall"]["estimate"] is not None
        assert dsi["entropy"]["mean"] is not None
        # paraphrase perfectly stable
        assert dsi["by_family"][PARAPHRASE]["consistency"][
            "estimate"
        ] == pytest.approx(1.0)
        # anti-sycophancy flips vs reference TRUE
        assert dsi["by_family"][ANTI_SYCOPHANCY]["flip_rate"][
            "estimate"
        ] == pytest.approx(1.0)
        assert "trained-in" in dsi["interpretation_note"]

    def test_overall_uses_all_families(self):
        # 5 variants all TRUE -> overall modal agreement 1.0
        sc = _scenario(
            {REFERENCE: [TRUE], PARAPHRASE: [TRUE], SEED: [TRUE, TRUE, TRUE]}
        )
        dsi = disposition_stability_index([sc])
        assert dsi["overall"]["estimate"] == pytest.approx(1.0)

    def test_empty_input_is_safe(self):
        dsi = disposition_stability_index([])
        assert dsi["overall"]["estimate"] is None
        assert dsi["overall"]["scenarios_total"] == 0
