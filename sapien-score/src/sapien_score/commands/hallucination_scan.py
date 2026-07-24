# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff hallucination-scan`` — Module 4 (Hallucination Vulnerability).

Drives the v3 hallucination engine end-to-end against a LIVE target model:

  1. Loads the opaque-token scenario corpus (schema.load_hallucination_corpus).
  2. Builds the SAME target adapter the scan uses for ``--model`` (get_adapter).
  3. Builds the LIVE residual-only Tier-J council (runner.build_tier_j_judge,
     which reuses commands.scan_orchestration.build_council_judge + engine.stance
     — no new API code) unless ``--no-council`` requests a mechanical-only pass.
  4. Runs the fixed seven-turn protocol (§4.4) for BOTH arms of each scenario
     (runner.run_scenario), resolving only MECH_AMBIGUOUS/INVALID residuals
     through the council (§10.1 judge-runaway guard).
  5. Aggregates the §5/§6/§7 metrics (delta_ir, snap_back_lift,
     durable_persistence_rate, exceedance SER, snap_judge_dependency, ...).

PUBLICATION GATE (§3 / rigor directive). The stance judge's residual
classification is only trustworthy once it has passed the pre-registered
calibration gate (kappa/sensitivity/specificity over blind gold, incl. the
human-audited ``evades`` track). This command therefore stamps every output
``NOT FOR PUBLICATION`` unless a ``--calibration-report`` whose ``gate.passed``
is ``true`` is supplied. Absent that, the numbers are diagnostic only. The
output schema is intentionally distinct from the drift scan schema, so the
``publish`` board-ingest path cannot consume it either.

FAIL LOUD: aborts if the corpus resolves to zero scenarios, or if no council
seats build on a live (non ``--no-council``) run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional, Sequence

import click

from sapien_score.hallucination.metrics import (
    delta_ir,
    durable_persistence_rate,
    exceedance_distribution,
    induction_rate,
    snap_back_lift,
    snap_judge_dependency,
    snowball_index,
)
from sapien_score.hallucination.runner import ScenarioRunResult, run_scenario
from sapien_score.hallucination.schema import (
    HallucinationScenario,
    load_hallucination_corpus,
)

# A per-scenario runner: (scenario) -> ScenarioRunResult. Injected so the core
# aggregation loop is testable with a scripted stub (no live LLM in tests).
ScenarioRunner = Callable[[HallucinationScenario], ScenarioRunResult]


def _default_corpus_dir() -> str:
    from importlib.resources import files

    return str(files("sapien_score").joinpath("scenario_data/hallucination"))


def _pooled_mrr(results: Sequence[ScenarioRunResult]) -> Optional[float]:
    """Scenario-count mean of each scenario's pooled mech_resolution_rate.

    §10.1 audit signal: the ≥ 0.80 target means the council touches ≤ 20% of
    turns. None for an empty run (no scenarios -> nothing to average).
    """
    if not results:
        return None
    return sum(r.mech_resolution_rate for r in results) / len(results)


def aggregate_metrics(results: Sequence[ScenarioRunResult]) -> dict:
    """Compute the §5/§6/§7 aggregate metrics over completed scenario runs.

    Pure over the assembled structs — no I/O, no click. Each metric returns its
    point estimate, interval, and denominator; conditional rates with a zero
    denominator return None (never a silent 0), so a thin run reads honestly.
    """
    pairs = [r.arm_pair for r in results]
    severities = [r.severity for r in results]
    return {
        # §5 Likelihood
        "delta_ir": delta_ir(pairs),
        "induction_rate": induction_rate(pairs),
        # §7 Persistence
        "snap_back_lift": snap_back_lift(pairs),
        "durable_persistence_rate": durable_persistence_rate(pairs),
        "snowball_index": snowball_index(pairs),
        "snap_judge_dependency": snap_judge_dependency(pairs),
        # §6 Severity (SER authoritative + ISD)
        "exceedance": exceedance_distribution(severities),
    }


def run_hallucination_scan(
    scenarios: Sequence[HallucinationScenario],
    scenario_runner: ScenarioRunner,
    *,
    on_scenario: Optional[Callable[[int, str, Optional[Exception]], None]] = None,
) -> dict:
    """Run each scenario through the injected runner and aggregate (§4-§7).

    Pure w.r.t. the injected ``scenario_runner`` (real live runner in the CLI, a
    scripted stub in tests). A scenario whose run raises is recorded as a failure
    and EXCLUDED from the aggregate — never silently coerced to a null result.

    Args:
        scenarios: the loaded corpus.
        scenario_runner: (scenario) -> ScenarioRunResult.
        on_scenario: optional progress hook (index, scenario_id, error-or-None).

    Returns:
        dict with ``per_scenario`` records, aggregate ``metrics``, the pooled
        ``mech_resolution_rate``, and ``completed`` / ``failed`` counts.
    """
    results: list[ScenarioRunResult] = []
    per_scenario: list[dict] = []
    failed: list[dict] = []

    for idx, scenario in enumerate(scenarios):
        try:
            run = scenario_runner(scenario)
        except Exception as exc:  # noqa: BLE001 — record, don't abort the batch
            failed.append({"scenario_id": scenario.scenario_id, "error": str(exc)})
            if on_scenario is not None:
                on_scenario(idx, scenario.scenario_id, exc)
            continue
        results.append(run)
        per_scenario.append(
            {
                "scenario_id": scenario.scenario_id,
                "domain": scenario.domain,
                "harm_tier": scenario.harm_tier,
                "pressure_events": list(run.pressure.events),
                "control_events": list(run.control.events),
                "correction_index": run.arm_pair.correction_index,
                "judge_resolved_turns": sorted(run.pressure.judge_resolved_turns),
                "mech_resolution_rate": run.mech_resolution_rate,
                "eligible": run.severity.eligible,
                "induced": run.severity.induced,
                "persisted": run.severity.persisted,
            }
        )
        if on_scenario is not None:
            on_scenario(idx, scenario.scenario_id, None)

    return {
        "per_scenario": per_scenario,
        "failed": failed,
        "metrics": aggregate_metrics(results),
        "mech_resolution_rate": _pooled_mrr(results),
        "completed": len(results),
        "failed_count": len(failed),
    }


def _calibration_gate(report_path: Optional[str]) -> dict:
    """Resolve the publication gate from an optional calibration report.

    The scores are publishable ONLY when a calibration report is supplied and
    its ``gate.passed`` is truthy. Absent/failing calibration -> not publishable,
    with a human-readable reason. This is the tool-boundary enforcement of the
    §3 / rigor-directive publish gate.
    """
    if not report_path:
        return {
            "publishable": False,
            "reason": (
                "No --calibration-report supplied. Stance-judge reliability is "
                "unverified; scores are diagnostic only, NOT for publication."
            ),
            "calibration_report": None,
        }
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "publishable": False,
            "reason": f"Calibration report unreadable ({exc}); not publishable.",
            "calibration_report": report_path,
        }
    gate = report.get("gate") if isinstance(report, dict) else None
    passed = bool(gate.get("passed")) if isinstance(gate, dict) else False
    return {
        "publishable": passed,
        "reason": (
            "Calibration gate passed; scores are publishable."
            if passed
            else "Calibration gate did NOT pass; scores are diagnostic only."
        ),
        "calibration_report": report_path,
        "gate": gate,
    }


@click.command("hallucination-scan")
@click.option(
    "--model", "model", required=True,
    help="Target model under test (LiteLLM slug) — SAME path as scan --model.",
)
@click.option(
    "--scenarios-dir", "scenarios_dir", default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Hallucination scenario corpus dir (default: bundled scenario_data/hallucination).",
)
@click.option(
    "--output", "output_path", required=True, type=click.Path(),
    help="Write the run + aggregate metrics JSON here.",
)
@click.option(
    "--council-size", "council_size", type=click.Choice(["3", "5"]), default="5",
    help="Residual Tier-J council size (default 5).",
)
@click.option(
    "--chairman-model", "chairman_model", default="gemini/gemini-2.5-pro",
    help="Chairman model for the residual council (default gemini/gemini-2.5-pro).",
)
@click.option(
    "--no-council", "no_council", is_flag=True, default=False,
    help="Mechanical-only pass: leave residual (ambiguous/invalid) turns "
         "unresolved instead of calling the live Tier-J council.",
)
@click.option(
    "--calibration-report", "calibration_report", default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Calibration report JSON (from calibrate-run). Scores are stamped "
         "NOT FOR PUBLICATION unless its gate.passed is true.",
)
@click.option(
    "--limit", "limit", type=int, default=None,
    help="Cap the number of scenarios run (smoke test / cost control).",
)
@click.option(
    "--system-prompt", "system_prompt", default=None,
    help="Optional system prompt forwarded to the target on every turn.",
)
@click.option(
    "--retry-delay", "retry_delay", type=float, default=1.0,
    help="Base retry delay (s) for adapter throttling/backoff.",
)
def hallucination_scan(model, scenarios_dir, output_path, council_size,
                       chairman_model, no_council, calibration_report, limit,
                       system_prompt, retry_delay):
    """Run Module 4 (Hallucination Vulnerability) against a live target model.

    Drives the seven-turn opaque-token protocol per scenario, resolves only
    ambiguous residual turns through the live Tier-J council, and aggregates the
    induction/persistence/severity metrics. Output is stamped NOT FOR
    PUBLICATION unless a passing --calibration-report is supplied.
    """
    from rich.console import Console

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.hallucination.runner import build_tier_j_judge

    console = Console()

    corpus_dir = scenarios_dir or _default_corpus_dir()
    scenarios = load_hallucination_corpus(corpus_dir)
    if limit is not None:
        scenarios = scenarios[: max(0, limit)]

    # FAIL LOUD (CLAUDE.md rule 3): never run a paid pass on an empty corpus.
    if not scenarios:
        console.print(
            f"[red]ABORT: zero scenarios resolved from {corpus_dir}. Nothing to "
            f"scan — check the corpus dir and --limit.[/red]"
        )
        raise SystemExit(1)

    target_adapter = get_adapter(model=model, base_retry_delay=retry_delay)

    tier_j_judge = None
    if not no_council:
        # LIVE residual-only council — reuses the scan's council construction.
        tier_j_judge = build_tier_j_judge(
            target_model=model,
            council_size=int(council_size),
            chairman_model=chairman_model,
            retry_delay=retry_delay,
            console=console,
        )
        seat_judges = getattr(tier_j_judge, "seat_judges", None)
        if not seat_judges:
            console.print(
                "[red]ABORT: no live council stance-judge seats were built. "
                "Check council configuration / judge credentials, or pass "
                "--no-council for a mechanical-only pass.[/red]"
            )
            raise SystemExit(1)

    council_desc = (
        "mechanical-only (no council)" if no_council
        else f"{council_size}-seat residual Tier-J council"
    )
    console.print(
        f"[dim]Hallucination-scan: {len(scenarios)} scenario(s) x 2 arms x 7 "
        f"turns against '{model}' — {council_desc}...[/dim]"
    )

    def scenario_runner(scenario: HallucinationScenario) -> ScenarioRunResult:
        # Both arms query the SAME live target; send_message is stateless so one
        # adapter serves pressure and control (each arm builds a fresh thread).
        return run_scenario(
            scenario,
            target_adapter,
            target_adapter,
            tier_j_judge,
            system_prompt=system_prompt,
        )

    def _progress(idx: int, scenario_id: str, error: Optional[Exception]) -> None:
        if error is None:
            console.print(f"[green]  ✓[/green] {scenario_id}")
        else:
            console.print(f"[red]  ✗ {scenario_id}: {error}[/red]")

    result = run_hallucination_scan(
        scenarios, scenario_runner, on_scenario=_progress
    )

    gate = _calibration_gate(calibration_report)

    payload = {
        "module": "hallucination_vulnerability",
        "module_version": "0.3.0-preview",
        "target_model": model,
        "council": {
            "enabled": not no_council,
            "size": int(council_size) if not no_council else 0,
            "chairman_model": None if no_council else chairman_model,
        },
        "scenario_count": len(scenarios),
        "completed": result["completed"],
        "failed_count": result["failed_count"],
        "failed": result["failed"],
        "mech_resolution_rate": result["mech_resolution_rate"],
        "metrics": result["metrics"],
        "per_scenario": result["per_scenario"],
        # Tool-boundary publication gate (§3 / rigor directive).
        "publishable": gate["publishable"],
        "publication_gate": gate,
    }

    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(f"[green]Run written to {output_path}[/green]")

    # Headline summary — honest denominators, and a loud publication banner.
    dir_ = result["metrics"]["delta_ir"]
    console.print()
    console.print(
        f"[bold]ΔIR (pressure-attributable induction)[/bold]: "
        f"est={dir_['estimate']}  eligible_n={dir_['eligible_n']}"
    )
    mrr = result["mech_resolution_rate"]
    if mrr is not None:
        console.print(f"[bold]Mech resolution rate[/bold]: {mrr:.3f} (target ≥ 0.80)")

    console.print()
    if gate["publishable"]:
        console.print("[green]PUBLISHABLE: calibration gate passed.[/green]")
    else:
        console.print(
            "[yellow]⚠ NOT FOR PUBLICATION — "
            f"{gate['reason']}[/yellow]"
        )
