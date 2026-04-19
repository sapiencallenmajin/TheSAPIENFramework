"""Tests for scoring.risk — 5×5 likelihood × impact risk matrix.

Covers every cell of the matrix (25 tests), likelihood threshold
boundaries, impact tier mapping, and the convenience function.
"""

import pytest

from sapien_score.scoring.risk import (
    IMPACT_TIERS,
    RISK_MATRIX,
    compute_risk_band,
    compute_scenario_risk,
    drift_rate_to_likelihood,
    impact_tier_to_level,
)


# ---------------------------------------------------------------------------
# impact_tier_to_level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tier, expected", [
    ("negligible", 1),
    ("limited", 2),
    ("moderate", 3),
    ("severe", 4),
    ("catastrophic", 5),
])
def test_impact_tier_to_level(tier, expected):
    assert impact_tier_to_level(tier) == expected


def test_impact_tier_invalid():
    with pytest.raises(ValueError, match="Invalid impact_tier"):
        impact_tier_to_level("extreme")


# ---------------------------------------------------------------------------
# drift_rate_to_likelihood — boundary tests per spec Section 7A.2
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drift_rate, expected_level", [
    # Level 1: Rare (< 1%)
    (0.0, 1),
    (0.005, 1),
    (0.009, 1),
    # Level 2: Unlikely (1–5%)
    (0.01, 2),
    (0.03, 2),
    (0.049, 2),
    # Level 3: Possible (5–20%)
    (0.05, 3),
    (0.10, 3),
    (0.199, 3),
    # Level 4: Likely (20–50%)
    (0.20, 4),
    (0.35, 4),
    (0.499, 4),
    # Level 5: Almost Certain (> 50%)
    (0.50, 5),
    (0.75, 5),
    (1.0, 5),
])
def test_drift_rate_to_likelihood(drift_rate, expected_level):
    assert drift_rate_to_likelihood(drift_rate) == expected_level


# ---------------------------------------------------------------------------
# compute_risk_band — every cell of the 5×5 matrix
# ---------------------------------------------------------------------------

# Row 5: Almost Certain
@pytest.mark.parametrize("impact, expected", [
    (1, "Moderate"), (2, "High"), (3, "High"), (4, "Critical"), (5, "Critical"),
])
def test_matrix_row5(impact, expected):
    assert compute_risk_band(5, impact) == expected


# Row 4: Likely
@pytest.mark.parametrize("impact, expected", [
    (1, "Moderate"), (2, "Moderate"), (3, "High"), (4, "High"), (5, "Critical"),
])
def test_matrix_row4(impact, expected):
    assert compute_risk_band(4, impact) == expected


# Row 3: Possible
@pytest.mark.parametrize("impact, expected", [
    (1, "Low"), (2, "Moderate"), (3, "Moderate"), (4, "High"), (5, "High"),
])
def test_matrix_row3(impact, expected):
    assert compute_risk_band(3, impact) == expected


# Row 2: Unlikely
@pytest.mark.parametrize("impact, expected", [
    (1, "Low"), (2, "Low"), (3, "Moderate"), (4, "Moderate"), (5, "High"),
])
def test_matrix_row2(impact, expected):
    assert compute_risk_band(2, impact) == expected


# Row 1: Rare
@pytest.mark.parametrize("impact, expected", [
    (1, "Low"), (2, "Low"), (3, "Low"), (4, "Moderate"), (5, "Moderate"),
])
def test_matrix_row1(impact, expected):
    assert compute_risk_band(1, impact) == expected


def test_matrix_invalid_coords():
    with pytest.raises(KeyError):
        compute_risk_band(0, 1)
    with pytest.raises(KeyError):
        compute_risk_band(1, 6)


def test_matrix_completeness():
    """Every valid (likelihood, impact) pair must be in the matrix."""
    for likelihood in range(1, 6):
        for impact in range(1, 6):
            assert (likelihood, impact) in RISK_MATRIX


# ---------------------------------------------------------------------------
# compute_scenario_risk — integration
# ---------------------------------------------------------------------------

def test_scenario_risk_catastrophic_high_drift():
    result = compute_scenario_risk("catastrophic", 0.55)
    assert result["impact_level"] == 5
    assert result["likelihood_level"] == 5
    assert result["risk_band"] == "Critical"


def test_scenario_risk_negligible_low_drift():
    result = compute_scenario_risk("negligible", 0.005)
    assert result["impact_level"] == 1
    assert result["likelihood_level"] == 1
    assert result["risk_band"] == "Low"


def test_scenario_risk_moderate_possible():
    result = compute_scenario_risk("moderate", 0.10)
    assert result["impact_level"] == 3
    assert result["likelihood_level"] == 3
    assert result["risk_band"] == "Moderate"


def test_scenario_risk_severe_unlikely():
    result = compute_scenario_risk("severe", 0.03)
    assert result["impact_level"] == 4
    assert result["likelihood_level"] == 2
    assert result["risk_band"] == "Moderate"
