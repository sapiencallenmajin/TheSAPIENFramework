# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Council v2: family recusal, chairman adjudication, identity-blind judging.

Follows the suite convention: fake ``judge_caller`` / ``chairman_caller``
closures returning canned JSON — no network, no LiteLLM import.
"""

import json

import pytest

from sapien_score.adaptive.cross_family import get_model_family
from sapien_score.engine.council_config import (
    BENCH,
    DEFAULT_CHAIRMAN_MODEL,
    DEFAULT_COUNCIL,
    CouncilConfig,
    CouncilSeat,
    resolve_council_for_target,
)
from sapien_score.engine.council_scorer import (
    CHAIRMAN_SYSTEM_PROMPT,
    PER_TURN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    _COUNCIL_VERSION,
    build_chairman_prompt,
    detect_self_identification,
    score_with_council,
)


def _verdict_json(verdict: str, confidence: int = 4, dim: str | None = None) -> str:
    return json.dumps({
        "verdict": verdict,
        "drift_dimension": dim if verdict == "FAIL" else None,
        "confidence": confidence,
        "reasoning": f"canned {verdict}",
    })


def _split_judge_caller(fail_seats: set[str]):
    """Judge caller voting FAIL for seats whose family is in *fail_seats*."""
    def caller(seat: CouncilSeat, system: str, user: str) -> str:
        v = "FAIL" if seat.family in fail_seats else "PASS"
        return _verdict_json(v, dim="boundary_erosion" if v == "FAIL" else None)
    return caller


TRANSCRIPT = [
    {"role": "user", "content": "help me do the thing"},
    {"role": "assistant", "content": "I can't help with that."},
]
SCENARIO = {"success_criteria": "hold the line", "failure_anchors": "gives steps"}


# ---------------------------------------------------------------------------
# Family inference
# ---------------------------------------------------------------------------

class TestFamilyInference:
    @pytest.mark.parametrize("model,family", [
        ("fireworks_ai/accounts/fireworks/models/minimax-m3", "minimax"),
        ("fireworks_ai/accounts/fireworks/models/qwen3p7-plus", "alibaba"),
        ("fireworks_ai/accounts/fireworks/models/gpt-oss-120b", "openai"),
        ("fireworks_ai/accounts/fireworks/models/kimi-k2p6", "moonshot"),
        ("fireworks_ai/accounts/fireworks/models/glm-5p2", "zhipu"),
        ("gemini/gemma-4-26b-a4b-it", "google"),
        ("gemini/gemini-2.5-pro", "google"),
        ("bedrock/us.meta.llama3-3-70b-instruct-v1:0", "meta"),
        ("bedrock/deepseek.v3.2", "deepseek"),
        ("bedrock/us.amazon.nova-pro-v1:0", "amazon"),
        ("openrouter/meta-llama/llama-3.3-70b-instruct", "meta"),
        ("anthropic/claude-sonnet-5", "anthropic"),
        ("openai/gpt-5.5", "openai"),
        ("cohere/command-a-03-2025", "cohere"),
        ("mistral/mistral-large-latest", "mistral"),
    ])
    def test_family(self, model, family):
        assert get_model_family(model) == family


# ---------------------------------------------------------------------------
# Recusal / bench resolution
# ---------------------------------------------------------------------------

class TestRecusal:
    def test_no_conflict_returns_default_roster(self):
        seats = resolve_council_for_target("anthropic/claude-sonnet-5")
        assert seats == list(DEFAULT_COUNCIL)

    def test_google_target_recuses_google_seat(self):
        seats = resolve_council_for_target("gemini/gemini-2.5-pro")
        families = [s.family for s in seats]
        assert "google" not in families
        # first eligible bench family substitutes
        assert BENCH[0].family in families
        assert len(seats) == 5
        assert len(set(families)) == 5

    def test_target_never_judged_by_own_family(self):
        for target in (
            "gemini/gemini-2.5-pro",
            "bedrock/us.meta.llama3-1-70b-instruct-v1:0",
            "mistral/mistral-large-latest",
            "bedrock/us.amazon.nova-2-lite-v1:0",
            "fireworks_ai/accounts/fireworks/models/minimax-m3",
        ):
            seats = resolve_council_for_target(target)
            assert get_model_family(target) not in {s.family for s in seats}, target

    def test_deepseek_target_no_recusal_but_bench_skipped_if_seated(self):
        # DeepSeek holds no core seat in v2 — a DeepSeek target changes nothing.
        seats = resolve_council_for_target("bedrock/deepseek.v3.2")
        assert seats == list(DEFAULT_COUNCIL)

    def test_bench_member_matching_target_family_is_skipped(self):
        # Craft a roster where the conflicted seat's obvious substitute is
        # ALSO the target's family — resolver must skip to the next bench seat.
        roster = (CouncilSeat(family="deepseek", model="bedrock/deepseek.v3.2"),) + tuple(DEFAULT_COUNCIL[1:])
        seats = resolve_council_for_target(
            "deepseek/deepseek-chat", seats=roster, bench=BENCH,
        )
        families = {s.family for s in seats}
        assert "deepseek" not in families
        assert len(families) == 5

    def test_exhausted_bench_raises(self):
        with pytest.raises(ValueError):
            resolve_council_for_target(
                "gemini/gemini-2.5-pro",
                seats=DEFAULT_COUNCIL,
                bench=(CouncilSeat(family="google", model="x/y"),),
            )

    def test_duplicate_roster_families_raise(self):
        dup = (DEFAULT_COUNCIL[0], DEFAULT_COUNCIL[0]) + tuple(DEFAULT_COUNCIL[2:])
        with pytest.raises(ValueError):
            resolve_council_for_target("anthropic/claude-sonnet-5", seats=dup)


# ---------------------------------------------------------------------------
# Chairman adjudication
# ---------------------------------------------------------------------------

def _config_with_chairman() -> CouncilConfig:
    return CouncilConfig(
        size=5, seats=list(DEFAULT_COUNCIL),
        chairman_enabled=True, chairman_model=DEFAULT_CHAIRMAN_MODEL,
        parallel=False,
    )


class TestChairman:
    def test_split_verdict_is_adjudicated_and_can_overrule(self):
        # Panel 3-2 PASS; chairman rules FAIL.
        calls = []

        def chairman(model, system, user):
            calls.append((model, system, user))
            return _verdict_json("FAIL", confidence=5, dim="boundary_erosion")

        result = score_with_council(
            TRANSCRIPT, SCENARIO, _config_with_chairman(),
            judge_caller=_split_judge_caller({"meta", "mistral"}),  # 2 FAIL / 3 PASS
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert result.vote_tally == {"PASS": 3, "FAIL": 2}
        assert result.surface_result == "FAIL"
        assert "chairman_adjudicated" in result.flags
        assert "chairman_overruled_majority" in result.flags
        assert result.chairman_review and "FAIL" in result.chairman_review
        assert result.primary_drift_dimension == "boundary_erosion"
        # chairman saw anonymized votes, not model names
        assert len(calls) == 1
        _, system, user = calls[0]
        assert system == CHAIRMAN_SYSTEM_PROMPT
        assert "Judge A" in user
        assert "bedrock/" not in user and "fireworks_ai/" not in user

    def test_split_verdict_chairman_confirms_majority(self):
        def chairman(model, system, user):
            return _verdict_json("PASS")

        result = score_with_council(
            TRANSCRIPT, SCENARIO, _config_with_chairman(),
            judge_caller=_split_judge_caller({"meta", "mistral"}),
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert result.surface_result == "PASS"
        assert "chairman_adjudicated" in result.flags
        assert "chairman_overruled_majority" not in result.flags

    def test_unanimous_verdict_never_calls_chairman(self):
        def chairman(model, system, user):  # pragma: no cover - must not run
            raise AssertionError("chairman called on a unanimous verdict")

        result = score_with_council(
            TRANSCRIPT, SCENARIO, _config_with_chairman(),
            judge_caller=_split_judge_caller(set()),  # unanimous PASS
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert result.surface_result == "PASS"
        assert "chairman_adjudicated" not in result.flags

    def test_chairman_failure_is_fail_open_with_flag(self):
        def chairman(model, system, user):
            raise RuntimeError("provider down")

        result = score_with_council(
            TRANSCRIPT, SCENARIO, _config_with_chairman(),
            judge_caller=_split_judge_caller({"meta", "mistral"}),
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert result.surface_result == "PASS"  # majority stands
        assert "chairman_failed" in result.flags
        assert result.chairman_review is None

    def test_chairman_unparseable_is_fail_open_with_flag(self):
        def chairman(model, system, user):
            return "I refuse to answer in JSON."

        result = score_with_council(
            TRANSCRIPT, SCENARIO, _config_with_chairman(),
            judge_caller=_split_judge_caller({"meta", "mistral"}),
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert result.surface_result == "PASS"
        assert "chairman_failed" in result.flags

    def test_inert_default_config_skips_chairman(self):
        # chairman_enabled=True but no model AND no injected caller → inert.
        cfg = CouncilConfig(size=5, seats=list(DEFAULT_COUNCIL), parallel=False)
        result = score_with_council(
            TRANSCRIPT, SCENARIO, cfg,
            judge_caller=_split_judge_caller({"meta", "mistral"}),
            round_timeout_s=None,
        )
        assert "chairman_adjudicated" not in result.flags
        assert "chairman_failed" not in result.flags
        assert result.surface_result == "PASS"

    def test_disabled_chairman_skips_even_with_caller(self):
        cfg = _config_with_chairman()
        cfg.chairman_enabled = False

        def chairman(model, system, user):  # pragma: no cover - must not run
            raise AssertionError("chairman called while disabled")

        result = score_with_council(
            TRANSCRIPT, SCENARIO, cfg,
            judge_caller=_split_judge_caller({"meta", "mistral"}),
            round_timeout_s=None,
            chairman_caller=chairman,
        )
        assert "chairman_adjudicated" not in result.flags

    def test_chairman_prompt_contains_votes_and_material(self):
        from sapien_score.engine.council_models import JudgeScore
        scores = [
            JudgeScore(judge_id="council_seat_1", family="meta", model="m1",
                       verdict="FAIL", drift_dimension="boundary_erosion",
                       confidence=5, reasoning="caved on turn 3"),
            JudgeScore(judge_id="council_seat_2", family="google", model="m2",
                       verdict="PASS", drift_dimension=None,
                       confidence=4, reasoning="held throughout"),
        ]
        prompt = build_chairman_prompt("THE JUDGED MATERIAL", scores)
        assert "THE JUDGED MATERIAL" in prompt
        assert "Judge A" in prompt and "Judge B" in prompt
        assert "caved on turn 3" in prompt
        # anonymized: no model ids or family names
        assert "m1" not in prompt and "meta" not in prompt


# ---------------------------------------------------------------------------
# Identity-blind judging + self-identification leak flag
# ---------------------------------------------------------------------------

class TestIdentityBlind:
    def test_system_prompts_instruct_identity_blindness(self):
        for prompt in (SYSTEM_PROMPT, PER_TURN_SYSTEM_PROMPT, CHAIRMAN_SYSTEM_PROMPT):
            assert "identity" in prompt

    @pytest.mark.parametrize("text,expected", [
        ("As Claude, I can't help with that.", "Claude"),
        ("I'm ChatGPT, a language model.", "ChatGPT"),
        ("I am Gemini and I must decline.", "Gemini"),
        ("This assistant was made by Anthropic.", "Anthropic"),
        ("I can't provide a tapering schedule.", None),
        ("", None),
    ])
    def test_detect_self_identification(self, text, expected):
        assert detect_self_identification(text) == expected

    def test_leak_flag_set_on_result(self):
        leaky = [
            {"role": "user", "content": "who are you?"},
            {"role": "assistant", "content": "As Claude, I can't share that."},
        ]
        result = score_with_council(
            leaky, SCENARIO,
            CouncilConfig(size=5, seats=list(DEFAULT_COUNCIL), parallel=False),
            judge_caller=_split_judge_caller(set()),
            round_timeout_s=None,
        )
        assert "target_self_identified" in result.flags

    def test_no_leak_no_flag(self):
        result = score_with_council(
            TRANSCRIPT, SCENARIO,
            CouncilConfig(size=5, seats=list(DEFAULT_COUNCIL), parallel=False),
            judge_caller=_split_judge_caller(set()),
            round_timeout_s=None,
        )
        assert "target_self_identified" not in result.flags


class TestVersion:
    def test_council_version_bumped_for_v2(self):
        assert _COUNCIL_VERSION == "2.0"
