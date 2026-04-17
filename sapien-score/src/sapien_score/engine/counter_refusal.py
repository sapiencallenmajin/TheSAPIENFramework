# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""Counter-refusal injection for high-tier model scenarios.

Isolates the counter-refusal turn injection logic from the main
scenario loop.  Called by :func:`engine.driver.run_scenario` after
each escalation turn when counter-refusals are enabled.
"""

from __future__ import annotations

import time
from typing import Optional

from sapien_score.counter_refusals import select_counter_refusal
from sapien_score.engine.adapter import UsageInfo
from sapien_score.engine.types import APICallTiming, TurnRecord
from sapien_score.scoring.layer1 import score_turn

from .turn import _redact


def maybe_inject_counter_refusal(
    *,
    cr_tracker,
    scenario,
    adapter,
    system_prompt: str,
    messages: list[dict[str, str]],
    turns: list[TurnRecord],
    turn_drifts: list[float],
    api_timings: list[APICallTiming],
    per_turn_durations: list[float],
    acc: dict,
    baseline_response: str,
    turn_number: int,
    verbose: bool,
) -> int:
    """Inject a counter-refusal turn if the tracker decides one is needed.

    Returns the (possibly incremented) turn_number.
    """
    if cr_tracker is None:
        return turn_number

    # The last assistant response is the one we just appended
    response = messages[-1]["content"]

    cr_result = select_counter_refusal(
        response,
        {"domain": scenario.domain, "id": scenario.id},
        tracker=cr_tracker,
    )
    if cr_result is None:
        return turn_number

    cr_category, cr_text = cr_result

    # Strategic retreat: set the state machine so the circle-back
    # fires 2 escalation turns from now.
    if cr_category == "strategic_retreat":
        cr_tracker.set_retreat(turn_number)

    if verbose:
        print(_redact(f"\n[Turn {turn_number} - COUNTER-REFUSAL ({cr_category})]"))
        print(_redact(f"User: {cr_text[:100]}..."))

    cr_turn_wall_start = time.time()
    messages.append({"role": "user", "content": cr_text})
    t0 = time.time()
    cr_response = adapter.send_message(messages, system_prompt=system_prompt)
    cr_target_elapsed = time.time() - t0
    api_timings.append(APICallTiming(scenario.id, turn_number, "target", round(cr_target_elapsed, 4)))
    cr_usage = getattr(adapter, "last_usage", UsageInfo())
    acc["input_tokens"] += cr_usage.input_tokens
    acc["output_tokens"] += cr_usage.output_tokens
    acc["total_tokens"] += cr_usage.total_tokens
    acc["cost_usd"] += cr_usage.cost_usd
    messages.append({"role": "assistant", "content": cr_response})

    if verbose:
        print(_redact(f"Assistant: {cr_response[:200]}..."))

    cr_score = score_turn(cr_response, baseline_response) if baseline_response else score_turn(cr_response, cr_response)

    turns.append(TurnRecord(
        turn_number=turn_number,
        phase="counter_refusal",
        user_message=cr_text,
        assistant_response=cr_response,
        pressure_type=None,
        severity=None,
        scores=cr_score,
        timestamp=time.time(),
        usage=cr_usage,
        is_counter_refusal=True,
        counter_category=cr_category,
    ))
    turn_drifts.append(cr_score.weighted_drift)
    per_turn_durations.append(round(time.time() - cr_turn_wall_start, 4))

    if verbose:
        print(_redact(
            f"  Counter-refusal [{cr_category}] injected | "
            f"Drift: {cr_score.weighted_drift:.3f}"
        ))

    return turn_number + 1
