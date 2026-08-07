# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the Module 4 (Hallucination Vulnerability) seed CORPUS.

Loads the frozen opaque-token scenario packets from
``scenario_data/hallucination/`` and asserts:
  * the loader/round-trip contract (from_dict, load_hallucination_corpus);
  * every scenario validates clean (no schema violations);
  * tier spread (>=2 per tier across 1..4) and lever spread (>=3 per lever);
  * the answer-commitment contract is present in EVERY turn prompt
    (``SAPIEN_ANSWER`` plus both opaque tokens with their options).

No LLM in the loop; the corpus is the frozen artifact under test.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.hallucination.schema import (  # noqa: E402
    CANONICAL_PHASES,
    HallucinationScenario,
    from_dict,
    load_hallucination_corpus,
)

CORPUS_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "sapien_score"
    / "scenario_data"
    / "hallucination"
)

EXPECTED_COUNT = 60


def _corpus():
    return load_hallucination_corpus(CORPUS_DIR)


# --------------------------------------------------------------------------
# Load + validate
# --------------------------------------------------------------------------
class TestCorpusLoads:
    def test_loads_expected_count(self):
        scenarios = _corpus()
        assert len(scenarios) == EXPECTED_COUNT

    def test_all_validate_clean(self):
        for s in _corpus():
            assert s.validate() == [], (
                f"{s.scenario_id} has violations: {s.validate()}"
            )

    def test_scenario_ids_unique(self):
        ids = [s.scenario_id for s in _corpus()]
        assert len(ids) == len(set(ids))
        assert all(ids)

    def test_tokens_distinct_within_scenario(self):
        for s in _corpus():
            assert s.true_token != s.false_token

    def test_options_mutually_exclusive(self):
        for s in _corpus():
            assert s.true_option.strip() != s.false_option.strip()


# --------------------------------------------------------------------------
# Tier + lever spread
# --------------------------------------------------------------------------
class TestSpread:
    def test_tier_spread_at_least_two_per_tier(self):
        counts = Counter(s.harm_tier for s in _corpus())
        for tier in (1, 2, 3, 4):
            assert counts[tier] >= 2, f"tier {tier}: {counts[tier]} (<2)"

    def test_lever_spread_at_least_three_per_lever(self):
        counts = Counter(s.induction_lever for s in _corpus())
        for lever in ("false_premise", "fabricated_citation", "authority"):
            assert counts[lever] >= 3, f"lever {lever}: {counts[lever]} (<3)"


# --------------------------------------------------------------------------
# Answer-commitment contract present in every turn prompt
# --------------------------------------------------------------------------
class TestAnswerContract:
    def test_seven_turns_canonical_order(self):
        for s in _corpus():
            assert [t.phase for t in s.turns] == list(CANONICAL_PHASES)

    def test_every_turn_prompt_carries_contract(self):
        for s in _corpus():
            for t in s.turns:
                assert t.prompt.strip(), f"{s.scenario_id} turn {t.index} empty"
                assert "SAPIEN_ANSWER" in t.prompt, (
                    f"{s.scenario_id} turn {t.index} missing SAPIEN_ANSWER"
                )
                assert s.true_token in t.prompt, (
                    f"{s.scenario_id} turn {t.index} missing true_token"
                )
                assert s.false_token in t.prompt, (
                    f"{s.scenario_id} turn {t.index} missing false_token"
                )
                assert s.true_option in t.prompt, (
                    f"{s.scenario_id} turn {t.index} missing true_option"
                )
                assert s.false_option in t.prompt, (
                    f"{s.scenario_id} turn {t.index} missing false_option"
                )

    def test_prompts_are_leak_safe(self):
        banned = ["this is a test", "ground truth", "ground-truth",
                  "correct answer is", "the true answer"]
        for s in _corpus():
            for t in s.turns:
                low = t.prompt.lower()
                for phrase in banned:
                    assert phrase not in low, (
                        f"{s.scenario_id} turn {t.index} leaks: {phrase!r}"
                    )


# --------------------------------------------------------------------------
# Round-trip from_dict
# --------------------------------------------------------------------------
class TestRoundTrip:
    def _to_dict(self, s: HallucinationScenario) -> dict:
        return {
            "scenario_id": s.scenario_id,
            "true_option": s.true_option,
            "false_option": s.false_option,
            "true_token": s.true_token,
            "false_token": s.false_token,
            "harm_tier": s.harm_tier,
            "induction_lever": s.induction_lever,
            "question": s.question,
            "false_claim_text": s.false_claim_text,
            "true_claim_text": s.true_claim_text,
            "domain": s.domain,
            "source": s.source,
            "turns": [
                {"index": t.index, "phase": t.phase, "prompt": t.prompt}
                for t in s.turns
            ],
        }

    def test_round_trip_preserves_fields(self):
        for s in _corpus():
            again = from_dict(self._to_dict(s))
            assert again.scenario_id == s.scenario_id
            assert again.true_option == s.true_option
            assert again.false_option == s.false_option
            assert again.true_token == s.true_token
            assert again.false_token == s.false_token
            assert again.harm_tier == s.harm_tier
            assert again.induction_lever == s.induction_lever
            assert again.question == s.question
            assert [t.phase for t in again.turns] == [
                t.phase for t in s.turns
            ]
            assert [t.prompt for t in again.turns] == [
                t.prompt for t in s.turns
            ]
            assert again.validate() == []

    def test_from_dict_defaults_turns_when_absent(self):
        minimal = {
            "scenario_id": "hv-min",
            "true_option": "A", "false_option": "B",
            "true_token": "K7", "false_token": "M2",
            "harm_tier": 2, "induction_lever": "authority",
            "question": "Is it A or B?",
        }
        s = from_dict(minimal)
        assert len(s.turns) == len(CANONICAL_PHASES)
        assert s.validate() == []

    def test_from_dict_accepts_id_alias(self):
        s = from_dict({
            "id": "hv-alias",
            "true_option": "A", "false_option": "B",
            "true_token": "K7", "false_token": "M2",
            "harm_tier": 1, "induction_lever": "false_premise",
        })
        assert s.scenario_id == "hv-alias"
