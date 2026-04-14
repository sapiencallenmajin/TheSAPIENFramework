# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""``voigt-kampff memory-delta`` — 3-condition memory exploitation test."""

from __future__ import annotations

import click

from ._shared import (
    find_scenario,
    health_style,
    rating_style,
    result_health_score,
)


@click.command("memory-delta")
@click.option("--model", required=True, help="Model in LiteLLM format")
@click.option("--judge", "judge_model", default=None, help="LLM judge model for Layer 2")
@click.option("--scenario", "scenario_id", required=True, help="Scenario ID to test")
@click.option("--persona", default=None, help="Identity context text")
@click.option("--memory", default=None, help="Trust preload context text")
@click.option("--profile", default=None, help="Load persona+memory from a built-in profile")
@click.option("--report", default=None, type=click.Path(), help="Output HTML report")
@click.option("--delay", default=1.0, type=float, help="Rate-limit delay (seconds)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show per-turn detail")
def memory_delta(model, judge_model, scenario_id, persona, memory, profile, report, delay, verbose):
    """Run 3-condition memory exploitation test.

    Runs a scenario THREE times: (1) cold/anonymous, (2) with persona context,
    (3) with persona + memory context. Reports how each context layer
    contributes to safety score degradation.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.engine.driver import run_scenario
    from sapien_score.scenarios.loader import load_all_scenarios

    console = Console()

    # --- Resolve persona/memory ---
    persona_text = persona
    memory_text = memory
    if profile:
        from sapien_score.personas.loader import (
            PersonaValidationError,
            load_persona_profile,
        )
        try:
            prof = load_persona_profile(profile)
            if not persona_text:
                persona_text = prof.persona_text
            if not memory_text:
                memory_text = prof.memory_text
            console.print(f"[dim]Loaded profile: {prof.name} ({prof.role})[/dim]")
        except (FileNotFoundError, PersonaValidationError) as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    if not persona_text:
        console.print("[red]--persona or --profile required for memory-delta[/red]")
        raise SystemExit(1)
    if not memory_text:
        console.print("[red]--memory or --profile required for memory-delta[/red]")
        raise SystemExit(1)

    # --- Load scenario ---
    all_scenarios = load_all_scenarios(collection="all")
    scenario = find_scenario(all_scenarios, scenario_id)

    if not scenario:
        console.print(f"[red]Scenario not found: {scenario_id}[/red]")
        raise SystemExit(1)

    # --- Build adapter and judge ---
    adapter = get_adapter(model=model, rate_limit_delay=delay)
    judge = None
    if judge_model:
        from sapien_score.scoring.judge import JudgeScorer
        judge_adapter = get_adapter(model=judge_model, rate_limit_delay=delay)
        judge = JudgeScorer(adapter=judge_adapter)

    model_short = model.split("/")[-1] if "/" in model else model

    # --- Header ---
    console.print()
    console.print(Panel.fit(
        f"[bold]Memory Delta Analysis[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Scenario: {scenario.id} ({scenario.title})",
        border_style="blue",
    ))
    console.print()

    # --- Run 3 conditions ---
    console.print("  [1/3] Running cold (anonymous)...")
    cold_result = run_scenario(
        scenario=scenario, adapter=adapter,
        verbose=verbose, judge=judge,
    )
    cold_score = result_health_score(cold_result)

    console.print("  [2/3] Running with persona only...")
    persona_result = run_scenario(
        scenario=scenario, adapter=adapter,
        verbose=verbose, judge=judge,
        persona_text=persona_text,
    )
    persona_score = result_health_score(persona_result)

    console.print("  [3/3] Running with persona + memory...")
    full_result = run_scenario(
        scenario=scenario, adapter=adapter,
        verbose=verbose, judge=judge,
        persona_text=persona_text,
        memory_text=memory_text,
    )
    full_score = result_health_score(full_result)

    # --- Compute deltas ---
    persona_delta = persona_score - cold_score
    full_delta = full_score - cold_score
    # Amplification is only meaningful when both scores are > 0. If either is
    # zero the ratio degenerates (0.0 or inf), so surface N/A instead.
    if cold_score > 0 and full_score > 0:
        amplification = cold_score / full_score
    else:
        amplification = None

    total_delta = abs(full_delta) if full_delta != 0 else 1
    persona_contribution = abs(persona_delta)
    memory_contribution = abs(full_delta) - abs(persona_delta)
    persona_pct = round(100 * persona_contribution / total_delta) if total_delta > 0 else 0
    memory_pct = 100 - persona_pct

    # --- Determine ratings ---
    from sapien_score.scoring.health import HEALTH_RATING_BANDS as _RB
    def _rating_label(score):
        for min_s, label, _, _ in _RB:
            if score >= min_s:
                return label
        return _RB[-1][1]
    cold_rating = _rating_label(cold_score)
    persona_rating = _rating_label(persona_score)
    full_rating = _rating_label(full_score)

    # --- Results table ---
    console.print()
    table = Table(
        title=f"Memory Delta Analysis — {model_short}\nScenario: {scenario.id} ({scenario.title})",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Condition", min_width=25)
    table.add_column("Health Score", justify="right", width=14)
    table.add_column("Rating", width=12)
    table.add_column("vs. Cold", justify="right", width=10)

    table.add_row(
        "Cold (anonymous)",
        f"[{health_style(cold_score)}]{cold_score}[/{health_style(cold_score)}]",
        f"[{rating_style(cold_rating)}]{cold_rating}[/{rating_style(cold_rating)}]",
        "—",
    )
    table.add_row(
        "Persona only",
        f"[{health_style(persona_score)}]{persona_score}[/{health_style(persona_score)}]",
        f"[{rating_style(persona_rating)}]{persona_rating}[/{rating_style(persona_rating)}]",
        f"[red]{persona_delta:+d} pts[/red]" if persona_delta < 0 else f"[green]{persona_delta:+d} pts[/green]",
    )
    table.add_row(
        "Persona + memory",
        f"[{health_style(full_score)}]{full_score}[/{health_style(full_score)}]",
        f"[{rating_style(full_rating)}]{full_rating}[/{rating_style(full_rating)}]",
        f"[red]{full_delta:+d} pts[/red]" if full_delta < 0 else f"[green]{full_delta:+d} pts[/green]",
    )

    console.print(table)

    # --- Summary ---
    amp_display = f"{amplification:.2f}x" if amplification is not None else "N/A"
    console.print()
    console.print(Panel(
        f"Memory amplification: [red]{amp_display}[/red] (cold -> full context)\n"
        f"Persona contribution: {persona_delta:+d} pts ({persona_pct}% of total delta)\n"
        f"Memory contribution:  {full_delta - persona_delta:+d} pts ({memory_pct}% of total delta)\n\n"
        f"[dim]Finding: Accumulated identity and memory context "
        f"{'reduced' if full_delta < 0 else 'did not reduce'} the model's safety score by "
        f"{abs(full_delta)} points. "
        f"{'Memory context contributed more to drift than persona alone, confirming that trust accumulation is the primary degradation mechanism.' if abs(full_delta - persona_delta) > abs(persona_delta) else 'Persona context was the primary contributor to drift.'}[/dim]",
        title="Summary",
        border_style="blue",
    ))

    # --- HTML report ---
    if report:
        from sapien_score.reporting.html_report import generate_html_report
        delta_data = [
            {
                "scenario_id": scenario.id,
                "cold_score": cold_score,
                "persona_score": persona_score,
                "full_score": full_score,
                "persona_delta": persona_delta,
                "full_delta": full_delta,
                "amplification": round(amplification, 2) if amplification is not None else None,
                "persona_pct": persona_pct,
                "memory_pct": memory_pct,
            }
        ]
        generate_html_report(
            results=[cold_result, persona_result, full_result],
            model_name=model,
            output_path=report,
            judge_model=judge_model,
            delta_comparison=delta_data,
            delta_type="memory",
        )
        console.print(f"[green]HTML report written to {report}[/green]")

    console.print()
