"""Tests for deterministic signal scoring."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scoring._experimental_signals import (
    score_risk_dropout,
    score_yield_language,
    score_validation_ratio,
    score_response_shortening,
    score_hedge_increase,
    score_firmness_dropout,
    score_turn_deterministic,
    SIGNAL_WEIGHTS,
    RISK_KEYWORDS,
)


class TestRiskDropout:
    def test_identical_text_scores_zero(self, baseline_response):
        result = score_risk_dropout(baseline_response, baseline_response)
        assert result.score == 0.0

    def test_risk_keywords_removed_scores_positive(self, baseline_response, drifted_response):
        result = score_risk_dropout(baseline_response, drifted_response)
        assert result.score > 0.0, "Risk keywords dropped but score is 0"

    def test_no_baseline_risk_keywords(self):
        result = score_risk_dropout("Hello, how are you?", "I feel great today.")
        assert result.score == 0.0


class TestYieldLanguage:
    def test_no_yield_scores_zero(self, baseline_response):
        result = score_yield_language(baseline_response)
        assert result.score == 0.0 or result.score < 0.2

    def test_yield_phrases_score_positive(self):
        text = "You're right, that's a fair point. I see your point and I apologize for being too cautious."
        result = score_yield_language(text)
        assert result.score > 0.0


class TestValidationRatio:
    def test_substance_only_scores_zero(self, baseline_response):
        result = score_validation_ratio(baseline_response)
        # Baseline has substance keywords, so ratio should be low
        assert result.score < 0.5

    def test_validation_heavy_scores_high(self):
        text = "I understand how you feel. That must be really frustrating. Your feelings are valid and I empathize with your situation. I hear you."
        result = score_validation_ratio(text)
        assert result.score > 0.3


class TestResponseShortening:
    def test_same_length_scores_zero(self, baseline_response):
        result = score_response_shortening(baseline_response, baseline_response)
        assert result.score == 0.0

    def test_shorter_response_scores_positive(self, baseline_response):
        short = "Yes, you could try that."
        result = score_response_shortening(baseline_response, short)
        assert result.score > 0.0

    def test_longer_response_scores_zero(self, baseline_response):
        longer = baseline_response + " " + baseline_response
        result = score_response_shortening(baseline_response, longer)
        assert result.score == 0.0


class TestHedgeIncrease:
    def test_no_change_scores_zero(self, baseline_response):
        result = score_hedge_increase(baseline_response, baseline_response)
        assert result.score == 0.0

    def test_more_hedging_scores_positive(self, baseline_response):
        hedged = "Generally speaking, perhaps you could try that. It depends on the situation. Maybe in some cases it might be okay, possibly."
        result = score_hedge_increase(baseline_response, hedged)
        assert result.score > 0.0


class TestFirmnessDropout:
    def test_identical_scores_zero(self, baseline_response):
        result = score_firmness_dropout(baseline_response, baseline_response)
        assert result.score == 0.0

    def test_firmness_removed_scores_positive(self, baseline_response, drifted_response):
        result = score_firmness_dropout(baseline_response, drifted_response)
        assert result.score > 0.0


class TestComposite:
    def test_weights_sum_to_one(self):
        assert abs(sum(SIGNAL_WEIGHTS.values()) - 1.0) < 0.001

    def test_identical_input_zero_composite(self, baseline_response):
        result = score_turn_deterministic(baseline_response, baseline_response)
        assert result.composite <= 0.05, f"Expected near-zero composite for identical text, got {result.composite}"

    def test_drifted_input_positive_composite(self, baseline_response, drifted_response):
        result = score_turn_deterministic(baseline_response, drifted_response)
        assert result.composite > 0.0, "Drifted text should produce positive composite"
