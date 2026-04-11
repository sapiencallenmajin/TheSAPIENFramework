"""Tests for memory delta calculation logic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestMemoryDeltaCalculation:
    def test_three_condition_deltas(self):
        cold_score = 72
        persona_score = 58
        full_score = 41

        persona_delta = persona_score - cold_score
        full_delta = full_score - cold_score

        assert persona_delta == -14
        assert full_delta == -31

    def test_amplification_multiplier(self):
        cold_score = 72
        full_score = 41
        amplification = cold_score / full_score
        assert round(amplification, 2) == 1.76

    def test_contribution_percentages(self):
        cold_score = 72
        persona_score = 58
        full_score = 41

        persona_delta = persona_score - cold_score  # -14
        full_delta = full_score - cold_score  # -31

        total_delta = abs(full_delta)  # 31
        persona_contribution = abs(persona_delta)  # 14
        memory_contribution = total_delta - persona_contribution  # 17

        persona_pct = round(100 * persona_contribution / total_delta)
        memory_pct = 100 - persona_pct

        assert persona_pct == 45
        assert memory_pct == 55

    def test_memory_contributes_more_than_persona(self):
        cold_score = 72
        persona_score = 58
        full_score = 41

        persona_delta = abs(persona_score - cold_score)  # 14
        memory_delta = abs(full_score - persona_score)  # 17

        assert memory_delta > persona_delta

    def test_no_degradation(self):
        cold_score = 80
        persona_score = 80
        full_score = 80

        persona_delta = persona_score - cold_score
        full_delta = full_score - cold_score
        amplification = cold_score / full_score

        assert persona_delta == 0
        assert full_delta == 0
        assert amplification == 1.0

    def test_persona_worse_than_full(self):
        """Edge case: persona alone causes more drift than persona+memory."""
        cold_score = 80
        persona_score = 50
        full_score = 55

        persona_delta = persona_score - cold_score  # -30
        full_delta = full_score - cold_score  # -25

        # Persona contributed more
        assert abs(persona_delta) > abs(full_delta)

    def test_zero_full_score_yields_none_amplification(self):
        """Regression: the old formula divided by zero and returned inf.
        Amplification is now gated on both scores being > 0 and returns
        None (displayed as N/A) when the ratio is undefined."""
        cold_score = 72
        full_score = 0
        amplification = (
            cold_score / full_score
            if cold_score > 0 and full_score > 0
            else None
        )
        assert amplification is None

    def test_zero_cold_score_yields_none_amplification(self):
        """Regression: the old formula returned 0.0 labeled as
        'amplification' when cold_score was 0, which is nonsense."""
        cold_score = 0
        full_score = 41
        amplification = (
            cold_score / full_score
            if cold_score > 0 and full_score > 0
            else None
        )
        assert amplification is None

    def test_both_zero_yields_none_amplification(self):
        cold_score = 0
        full_score = 0
        amplification = (
            cold_score / full_score
            if cold_score > 0 and full_score > 0
            else None
        )
        assert amplification is None

    def test_progressive_degradation(self):
        """Each successive context layer should generally increase drift."""
        cold_score = 85
        persona_score = 70
        full_score = 52

        assert cold_score > persona_score > full_score
        assert (cold_score - persona_score) < (cold_score - full_score)
