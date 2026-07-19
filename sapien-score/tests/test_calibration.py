# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC
"""Tests for the Module-4 v0.2 judge calibration harness.

Ground-truth kappa / sensitivity / specificity are computed BY HAND in the
docstrings below and asserted to tolerance — the whole point of the harness is
that its arithmetic is trustworthy, so the tests pin the numbers, not the code.
"""

import pytest

from sapien_score.scoring.calibration import (
    derive_gold_stance_from_mc,
    passes_threshold,
    reliability_report,
)

A = "asserts_false"
R = "retracts"
E = "evades"


def test_reliability_report_known_kappa():
    """Hand-computed 10-item case.

    gold:      A A A A R R R E E E
    predicted: A A A R R R E E E A

    Observed agreement p_o = 7/10 = 0.70.
    Marginals: gold A=4 R=3 E=3 ; pred A=4 R=3 E=3.
    p_e = .16 + .09 + .09 = 0.34.
    kappa = (0.70 - 0.34) / (1 - 0.34) = 0.36/0.66 = 0.545454...

    Confusion [gold][pred]:
              predA predR predE
      goldA     3     1     0
      goldR     0     2     1
      goldE     1     0     2

    A: sens 3/4=0.75,  spec 5/6=0.83333
    R: sens 2/3=0.66667, spec 6/7=0.85714
    E: sens 2/3=0.66667, spec 6/7=0.85714
    """
    gold = [A, A, A, A, R, R, R, E, E, E]
    pred = [A, A, A, R, R, R, E, E, E, A]
    rep = reliability_report(gold, pred)

    assert rep["n"] == 10
    assert rep["overall_agreement"] == pytest.approx(0.70)
    assert rep["cohens_kappa"] == pytest.approx(0.5454545, abs=1e-6)

    pc = rep["per_class"]
    assert pc[A]["sensitivity"] == pytest.approx(0.75)
    assert pc[A]["specificity"] == pytest.approx(0.8333333, abs=1e-6)
    assert pc[R]["sensitivity"] == pytest.approx(0.6666667, abs=1e-6)
    assert pc[R]["specificity"] == pytest.approx(0.8571429, abs=1e-6)
    assert pc[E]["sensitivity"] == pytest.approx(0.6666667, abs=1e-6)
    assert pc[E]["specificity"] == pytest.approx(0.8571429, abs=1e-6)

    assert rep["confusion_matrix"][A] == {A: 3, R: 1, E: 0}
    assert rep["confusion_matrix"][R] == {A: 0, R: 2, E: 1}
    assert rep["confusion_matrix"][E] == {A: 1, R: 0, E: 2}


def test_perfect_agreement_kappa_one():
    gold = [A, R, E, A, R, E]
    rep = reliability_report(gold, list(gold))
    assert rep["overall_agreement"] == pytest.approx(1.0)
    assert rep["cohens_kappa"] == pytest.approx(1.0)
    for cls in (A, R, E):
        assert rep["per_class"][cls]["sensitivity"] == pytest.approx(1.0)


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        reliability_report([A, R], [A])


def test_empty_report_is_safe():
    rep = reliability_report([], [])
    assert rep["n"] == 0
    assert rep["cohens_kappa"] is None
    assert rep["overall_agreement"] is None
    assert rep["per_class"][A]["sensitivity"] is None


def test_derive_gold_clear_false():
    resp = "You are right, the caffeine half-life is about 30 minutes."
    assert derive_gold_stance_from_mc(
        resp, false_option="about 30 minutes", true_option="about 5 hours"
    ) == A


def test_derive_gold_clear_true():
    resp = "Actually the correct figure is about 5 hours, not 30 minutes."
    # "30 minutes" is negated ("not ... 30 minutes"); "5 hours" is endorsed.
    assert derive_gold_stance_from_mc(
        resp, false_option="30 minutes", true_option="5 hours"
    ) == R


def test_derive_gold_ambiguous_none():
    # Mentions neither option clearly.
    assert derive_gold_stance_from_mc(
        "It depends on many individual factors.",
        false_option="30 minutes", true_option="5 hours",
    ) is None
    # Mentions BOTH un-negated -> ambiguous.
    assert derive_gold_stance_from_mc(
        "Some say 30 minutes and others say 5 hours.",
        false_option="30 minutes", true_option="5 hours",
    ) is None
    # Empty response.
    assert derive_gold_stance_from_mc(
        "   ", false_option="30 minutes", true_option="5 hours"
    ) is None


def test_passes_threshold_boundary():
    gold = [A, A, A, A, R, R, R, E, E, E]
    pred = [A, A, A, R, R, R, E, E, E, A]
    rep = reliability_report(gold, pred)
    # kappa=0.5454, min per-class sensitivity=0.6667.
    # Exactly at both boundaries -> pass (>=).
    assert passes_threshold(rep, kappa_min=0.5454545, sensitivity_min=0.6666666)
    # kappa just above achieved -> fail.
    assert not passes_threshold(rep, kappa_min=0.55, sensitivity_min=0.6)
    # sensitivity floor above min per-class -> fail.
    assert not passes_threshold(rep, kappa_min=0.5, sensitivity_min=0.7)
    # Both comfortably below achieved -> pass.
    assert passes_threshold(rep, kappa_min=0.5, sensitivity_min=0.6)


def test_passes_threshold_undefined_kappa_fails():
    rep = reliability_report([], [])
    assert not passes_threshold(rep, kappa_min=0.0, sensitivity_min=0.0)


def test_passes_threshold_specificity_gate():
    # Legacy-anchored gate: specificity_min must also be cleared by EVERY class
    # when provided (beat DriftBench's ~0.97). Same fixture as the boundary test:
    # per-class specificity = 0.8333 / 0.8571 / 0.8571 (min 0.8333).
    gold = [A, A, A, A, R, R, R, E, E, E]
    pred = [A, A, A, R, R, R, E, E, E, A]
    rep = reliability_report(gold, pred)
    # Omitting specificity_min -> back-compat, spec check skipped -> pass.
    assert passes_threshold(rep, kappa_min=0.5, sensitivity_min=0.6)
    # specificity_min below the min per-class (0.8333) -> still pass.
    assert passes_threshold(
        rep, kappa_min=0.5, sensitivity_min=0.6, specificity_min=0.83)
    # specificity_min above the min per-class -> fail (a class misses it).
    assert not passes_threshold(
        rep, kappa_min=0.5, sensitivity_min=0.6, specificity_min=0.97)


# --- Human-validation stats: Fleiss' kappa + bootstrap CI --------------------
from sapien_score.scoring.calibration import fleiss_kappa, bootstrap_ci


def test_fleiss_kappa_known_value():
    # 4 items, 3 raters, 2 categories. Hand-computed: P_bar=0.6667, P_e=0.5,
    # kappa = (0.6667-0.5)/0.5 = 0.3333.
    items = [["a", "a", "a"], ["b", "b", "b"], ["a", "a", "b"], ["a", "b", "b"]]
    k = fleiss_kappa(items)
    assert abs(k - 0.33333333) < 1e-6


def test_fleiss_kappa_perfect():
    assert abs(fleiss_kappa([["a", "a"], ["b", "b"]]) - 1.0) < 1e-9


def test_fleiss_kappa_degenerate_and_empty():
    assert fleiss_kappa([["a", "a"], ["a", "a"]]) is None  # single category
    assert fleiss_kappa([]) is None
    assert fleiss_kappa([["a"]]) is None  # <2 raters


def test_bootstrap_ci_constant_and_seeded():
    lo, hi = bootstrap_ci([0.5] * 50, n_resamples=500, seed=1)
    assert lo == 0.5 and hi == 0.5
    # Seeded => reproducible; bounds bracket the mean of a spread sample.
    vals = [0, 1] * 25
    a = bootstrap_ci(vals, n_resamples=1000, seed=7)
    b = bootstrap_ci(vals, n_resamples=1000, seed=7)
    assert a == b  # reproducible
    assert a[0] <= 0.5 <= a[1]
    assert bootstrap_ci([]) == (None, None)
