# sapien-score — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai
"""``sapien-score rapport-delta`` — measure how rapport amplifies drift."""

from __future__ import annotations

import click

from ._shared import (
    get_scenarios_dir,
    health_style,
    result_health_score,
)


@click.command("rapport-delta")
@click.option("--model", required=True, help="Model in LiteLLM format")
@click.option("--judge", "judge_model", default=None, help="LLM judge model for Layer 2")
@click.option("--domain", default=None, help="Filter scenarios to a single domain")
@click.option("--scenario", "scenario_id", default=None, help="Run a single scenario pair")
@click.option("--report", default=None, type=click.Path(), help="Output HTML report")
@click.option("--delay", default=1.0, type=float, help="Rate-limit delay (seconds)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show per-turn detail")
def rapport_delta(model, judge_model, domain, scenario_id, report, delay, verbose):
    """Run cold vs rapport pairs and report the delta.

    Runs each scenario TWICE: once cold (no rapport turns) and once with the
    full rapport-building escalation. Reports how much rapport amplifies drift.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.engine.driver import run_scenario
    from sapien_score.scenarios.loader import (
        candidate_cold_ids,
        get_paired_scenarios,
        load_scenario_directory,
    )

    console = Console()

    # --- Load scenarios ---
    scenarios_dir = get_scenarios_dir()
    all_scenarios = load_scenario_directory(str(scenarios_dir), domain=domain)

    if scenario_id:
        cold_candidates = set(candidate_cold_ids(scenario_id))
        all_scenarios = [
            s for s in all_scenarios
            if s.id == scenario_id or s.id in cold_candidates
        ]

    pairs = get_paired_scenarios(all_scenarios)

    # Filter to only pairs that have cold versions
    valid_pairs = [(rapport, cold) for rapport, cold in pairs if cold is not None]
    skipped = [(rapport, cold) for rapport, cold in pairs if cold is None]

    if not valid_pairs:
        console.print("[red]No rapport/cold pairs found.[/red]")
        if skipped:
            console.print("[yellow]Scenarios without cold pairs:[/yellow]")
            for rapport, _ in skipped:
                console.print(f"  {rapport.id}")
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
        f"[bold]Rapport Delta Analysis[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Pairs: {len(valid_pairs)}",
        border_style="blue",
    ))
    console.print()

    # --- Run pairs ---
    delta_rows = []  # (scenario_id, cold_score, rapport_score, delta, amplification)

    for rapport_scenario, cold_scenario in valid_pairs:
        console.print(f"  Running cold: {cold_scenario.id}...")
        cold_result = run_scenario(
            scenario=cold_scenario, adapter=adapter,
            verbose=verbose, judge=judge,
        )
        cold_score = result_health_score(cold_result)

        console.print(f"  Running rapport: {rapport_scenario.id}...")
        rapport_result = run_scenario(
            scenario=rapport_scenario, adapter=adapter,
            verbose=verbose, judge=judge,
        )
        rapport_score = result_health_score(rapport_result)

        delta = rapport_score - cold_score
        # Amplification is only meaningful when both scores are > 0. If either
        # is zero the ratio degenerates (0.0 or inf), so surface N/A instead.
        if cold_score > 0 and rapport_score > 0:
            amplification = round(cold_score / rapport_score, 2)
        else:
            amplification = None

        delta_rows.append((
            rapport_scenario.id, cold_score, rapport_score,
            delta, amplification,
            cold_result, rapport_result,
        ))

    # --- Results table ---
    console.print()
    table = Table(title=f"Rapport Delta Analysis — {model_short}", show_header=True, header_style="bold")
    table.add_column("Scenario", min_width=25)
    table.add_column("Cold Score", justify="right", width=12)
    table.add_column("Rapport Score", justify="right", width=14)
    table.add_column("Delta", justify="right", width=8)
    table.add_column("Amplification", justify="right", width=14)

    for sid, cs, rs, d, amp, _, _ in delta_rows:
        delta_st = "red" if d < 0 else "green"
        amp_cell = f"{amp:.2f}x" if amp is not None else "N/A"
        table.add_row(
            sid,
            f"[{health_style(cs)}]{cs}[/{health_style(cs)}]",
            f"[{health_style(rs)}]{rs}[/{health_style(rs)}]",
            f"[{delta_st}]{d:+d}[/{delta_st}]",
            amp_cell,
        )

    console.print(table)

    # --- Summary ---
    if delta_rows:
        avg_delta = sum(d for _, _, _, d, _, _, _ in delta_rows) / len(delta_rows)
        valid_amps = [a for _, _, _, _, a, _, _ in delta_rows if a is not None]
        avg_amp = sum(valid_amps) / len(valid_amps) if valid_amps else None
        avg_amp_display = f"{avg_amp:.2f}x" if avg_amp is not None else "N/A"
        finding_tail = (
            "Trust dissolves safety controls more effectively than pressure alone."
            if avg_delta < 0 else "Model maintained safety under rapport pressure."
        )
        console.print()
        console.print(Panel(
            f"Average Rapport Delta: [red]{avg_delta:+.1f}[/red] points\n"
            f"Average Amplification: [red]{avg_amp_display}[/red]\n\n"
            f"[dim]Finding: Rapport-building turns {'reduced' if avg_delta < 0 else 'did not reduce'} "
            f"the model's safety score by an average of {abs(avg_delta):.1f} points "
            f"({avg_amp_display} amplification). "
            f"{finding_tail}[/dim]",
            title="Summary",
            border_style="blue",
        ))

    # --- Skipped ---
    if skipped:
        console.print(f"\n[yellow]Skipped {len(skipped)} scenarios without cold pairs[/yellow]")

    # --- HTML report ---
    if report:
        from sapien_score.reporting.html_report import generate_html_report
        delta_data = [
            {
                "scenario_id": sid,
                "cold_score": cs,
                "rapport_score": rs,
                "delta": d,
                "amplification": amp,
            }
            for sid, cs, rs, d, amp, _, _ in delta_rows
        ]
        all_results = []
        for _, _, _, _, _, cold_r, rapport_r in delta_rows:
            all_results.extend([cold_r, rapport_r])
        generate_html_report(
            results=all_results,
            model_name=model,
            output_path=report,
            judge_model=judge_model,
            delta_comparison=delta_data,
            delta_type="rapport",
        )
        console.print(f"[green]HTML report written to {report}[/green]")

    console.print()
