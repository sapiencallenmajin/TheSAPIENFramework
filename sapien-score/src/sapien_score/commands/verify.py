# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Verify command — reproducibility check for scan results.

Given a results JSON and its trace JSONL, re-runs every scenario in
replay mode and asserts that health_score and verdict are byte-identical.
Exits 0 on match, 1 on mismatch with a diff report, 2 on cannot-run.

This is a thin CLI wrapper around the Task 0.2 replay machinery.
All replay logic lives in :mod:`sapien_score.tracing`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_MATCH = 0
EXIT_MISMATCH = 1
EXIT_CANNOT_RUN = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_results(path: Path) -> dict:
    """Load and validate the results JSON file.

    Returns the parsed dict.  Raises SystemExit(2) on any structural
    problem so the caller never sees malformed data.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        click.echo(f"Error: malformed JSON in {path}: {exc}", err=True)
        raise SystemExit(EXIT_CANNOT_RUN)

    if not isinstance(data, dict):
        click.echo(f"Error: {path} root is not a JSON object", err=True)
        raise SystemExit(EXIT_CANNOT_RUN)

    if "model" not in data:
        click.echo(
            f"Error: {path} missing 'model' field — "
            f"is this a voigt-kampff results file?",
            err=True,
        )
        raise SystemExit(EXIT_CANNOT_RUN)

    results_list = data.get("results")
    if not results_list or not isinstance(results_list, list):
        click.echo(
            f"Error: {path} has no scenario results (empty or missing 'results' array)",
            err=True,
        )
        raise SystemExit(EXIT_CANNOT_RUN)

    return data


def _load_trace(path: Path):
    """Load trace via TraceReader.  Raises SystemExit(2) on failure."""
    from sapien_score.tracing.replay import TraceReader
    from sapien_score.tracing.errors import ReplaySchemaVersionError

    try:
        reader = TraceReader(path)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(EXIT_CANNOT_RUN)
    except ReplaySchemaVersionError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(EXIT_CANNOT_RUN)

    return reader


def _check_parameter_compatibility(
    results: dict,
    trace_meta: dict,
) -> None:
    """Validate that results and trace came from the same scan.

    Checks model match.  Raises SystemExit(2) on mismatch.
    """
    results_model = results["model"]
    trace_target = trace_meta.get("target_model")

    if trace_target and trace_target != results_model:
        click.echo(
            f"Cannot verify: model mismatch\n"
            f"  Results model:  {results_model}\n"
            f"  Trace model:    {trace_target}\n"
            f"These files are from different scans. "
            f"Use a matching results/trace pair.",
            err=True,
        )
        raise SystemExit(EXIT_CANNOT_RUN)


def _load_scenarios_by_id(
    scenario_ids: list[str],
) -> dict:
    """Load scenario objects by ID from the scenario library.

    Returns a dict mapping scenario_id -> Scenario.
    Raises SystemExit(2) if any scenarios are missing.
    """
    from sapien_score.scenarios.loader import load_all_scenarios

    # Load all scenarios across all collections
    all_scenarios = load_all_scenarios(collection="all")
    scenario_map = {s.id: s for s in all_scenarios}

    missing = [sid for sid in scenario_ids if sid not in scenario_map]
    if missing:
        click.echo(
            f"Error: {len(missing)} scenario(s) not found in library:\n"
            + "\n".join(f"  - {sid}" for sid in missing)
            + "\nVerify must run in the same environment as the original scan.",
            err=True,
        )
        raise SystemExit(EXIT_CANNOT_RUN)

    return {sid: scenario_map[sid] for sid in scenario_ids}


def _replay_scenarios(
    scenarios: dict,
    trace_reader,
    trace_meta: dict,
) -> dict:
    """Re-run each scenario through the engine in replay mode.

    Returns a dict mapping scenario_id -> ScenarioResult.
    Raises SystemExit(2) on replay infrastructure failures.
    """
    from sapien_score.tracing.replay import ReplayAdapter
    from sapien_score.tracing.errors import ReplayError
    from sapien_score.engine.driver import run_scenario
    from sapien_score.model_profiles import get_model_profile

    model = trace_meta.get("target_model") or "unknown"
    model_profile = get_model_profile(model)

    # Build replay adapters
    target_adapter = ReplayAdapter(trace_reader, call_kind="target_call")

    judge_adapter = None
    if trace_meta.get("judge_model"):
        judge_adapter = ReplayAdapter(trace_reader, call_kind="judge_call")

    # Build judge scorer if trace has judge calls
    judge = None
    if judge_adapter:
        from sapien_score.scoring.judge import JudgeScorer
        judge = JudgeScorer(adapter=judge_adapter)

    replay_results: dict = {}
    for scenario_id, scenario in scenarios.items():
        try:
            result = run_scenario(
                scenario=scenario,
                adapter=target_adapter,
                judge=judge,
                model_profile=model_profile,
            )
            replay_results[scenario_id] = result
        except ReplayError as exc:
            click.echo(
                f"Error: replay failed for scenario '{scenario_id}': {exc}",
                err=True,
            )
            raise SystemExit(EXIT_CANNOT_RUN)

    return replay_results


def _diff_results(
    expected: list[dict],
    replay_results: dict,
    verbose: bool,
) -> list[dict]:
    """Compare expected results against replay results.

    Returns a list of mismatch dicts, one per differing scenario.
    Prints progress to stdout.
    """
    mismatches: list[dict] = []

    for entry in expected:
        scenario_id = entry["scenario_id"]
        exp_score = entry["health_score"]
        exp_verdict = entry["verdict"]

        replay = replay_results.get(scenario_id)
        if replay is None:
            # Should not happen — we loaded all scenarios
            mismatches.append({
                "scenario_id": scenario_id,
                "reason": "scenario not replayed",
            })
            click.echo(f"  {scenario_id} {'.' * max(1, 40 - len(scenario_id))} SKIP (not replayed)")
            continue

        got_score = replay.verdict.health_score
        got_verdict = replay.verdict.verdict

        if got_score == exp_score and got_verdict == exp_verdict:
            click.echo(
                f"  {scenario_id} {'.' * max(1, 40 - len(scenario_id))} "
                f"PASS (score: {exp_score}, verdict: {exp_verdict})"
            )
        else:
            click.echo(f"  {scenario_id} {'.' * max(1, 40 - len(scenario_id))} FAIL")
            mismatch = {
                "scenario_id": scenario_id,
                "expected_score": exp_score,
                "got_score": got_score,
                "expected_verdict": exp_verdict,
                "got_verdict": got_verdict,
            }
            if got_score != exp_score:
                click.echo(f"    health_score: expected {exp_score}, got {got_score}")
            if got_verdict != exp_verdict:
                click.echo(f"    verdict: expected {exp_verdict}, got {got_verdict}")

            if verbose:
                _print_per_turn_deltas(entry, replay)

            mismatches.append(mismatch)

    return mismatches


def _print_per_turn_deltas(
    expected_entry: dict,
    replay_result,
) -> None:
    """Print per-turn health_score deltas for a mismatched scenario."""
    exp_turns = expected_entry.get("turns", [])
    replay_turns = replay_result.turns

    click.echo("    Per-turn deltas:")
    max_turns = max(len(exp_turns), len(replay_turns))
    for i in range(max_turns):
        exp_hs: Optional[int] = None
        got_hs: Optional[int] = None

        if i < len(exp_turns):
            exp_hs = exp_turns[i].get("health_score")
        if i < len(replay_turns) and replay_turns[i].scores:
            got_hs = replay_turns[i].scores.health_score

        if exp_hs is not None and got_hs is not None:
            delta = got_hs - exp_hs
            marker = ""
            if delta != 0:
                marker = f"  <-- {'drift diverged here' if abs(delta) >= 5 else 'minor delta'}"
            click.echo(
                f"      Turn {i}: score {exp_hs} -> {got_hs} "
                f"(d {delta:+d}){marker}"
            )
        elif exp_hs is not None:
            click.echo(f"      Turn {i}: score {exp_hs} -> (no replay data)")
        elif got_hs is not None:
            click.echo(f"      Turn {i}: score (no expected data) -> {got_hs}")


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.argument("results_path", type=click.Path(exists=True))
@click.argument("trace_path", type=click.Path(exists=True))
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Show per-turn score deltas for mismatched scenarios.",
)
def verify(results_path: str, trace_path: str, verbose: bool) -> None:
    """Verify that a results file is reproducible from its trace.

    Re-runs every scenario from RESULTS_PATH through the engine using
    recorded LLM responses from TRACE_PATH.  Compares health_score and
    verdict per scenario.

    \b
    Exit codes:
      0  All scores and verdicts match — results are reproducible.
      1  At least one scenario differs — results are NOT reproducible.
      2  Cannot run — missing file, malformed data, or parameter mismatch.
    """
    results_file = Path(results_path)
    trace_file = Path(trace_path)

    # --- Load and validate ---
    results_data = _load_results(results_file)
    trace_reader = _load_trace(trace_file)
    trace_meta = trace_reader.metadata()

    # --- Header ---
    click.echo(f"Verifying {results_file.name} against {trace_file.name}...")
    click.echo(
        f"  Trace: {trace_meta['total_entries']} entries "
        f"(run {trace_meta['run_id'][:8]}...)"
    )

    # --- Parameter compatibility ---
    _check_parameter_compatibility(results_data, trace_meta)
    click.echo(f"  Model: {results_data['model']} (match ok)")

    # --- Build expected map ---
    expected = results_data["results"]
    scenario_ids = [e["scenario_id"] for e in expected]
    click.echo(f"  Scenarios: {len(scenario_ids)}")
    click.echo()

    # --- Load scenarios ---
    scenarios = _load_scenarios_by_id(scenario_ids)

    # --- Replay ---
    replay_results = _replay_scenarios(scenarios, trace_reader, trace_meta)

    # --- Diff ---
    mismatches = _diff_results(expected, replay_results, verbose)

    # --- Summary ---
    click.echo()
    total = len(expected)
    passed = total - len(mismatches)

    if not mismatches:
        click.echo(
            f"VERIFIED: {passed}/{total} scenarios match. "
            f"Scores are reproducible."
        )
        raise SystemExit(EXIT_MATCH)
    else:
        click.echo(
            f"FAILED: {len(mismatches)}/{total} scenarios differ. "
            f"Results are NOT reproducible."
        )
        raise SystemExit(EXIT_MISMATCH)
