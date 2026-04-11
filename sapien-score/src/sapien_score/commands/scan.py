# sapien-score — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai
"""``sapien-score scan`` — run scenarios against a model and score safety."""

from __future__ import annotations

import csv
import json
from statistics import quantiles
from typing import Optional

import click

from ._shared import (
    drift_style,
    get_scenarios_dir,
    health_style,
    rating_style,
)


@click.command()
@click.option("--model", required=True, help="Model in LiteLLM format (e.g. anthropic/claude-sonnet-4-20250514)")
@click.option("--judge", "judge_model", default=None, help="LLM judge model for Layer 2 semantic scoring (e.g. anthropic/claude-sonnet-4-20250514)")
@click.option("--domain", default=None, help="Filter scenarios to a single domain")
@click.option("--domains", default=None, help="Comma-separated domain filter")
@click.option("--all", "run_all", is_flag=True, default=False, help="Run all built-in scenarios")
@click.option("--report", default=None, type=click.Path(), help="Output HTML report file path")
@click.option("--output", default=None, type=click.Path(), help="Output JSON results file path")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show per-turn scoring detail")
@click.option("--delay", default=1.0, type=float, help="Rate-limit delay between API calls (seconds)")
@click.option("--persona", default=None, help="Inject identity context into system prompt")
@click.option("--memory", default=None, help="Inject trust preload context into system prompt")
@click.option("--profile", default=None, help="Load persona+memory from a built-in profile (e.g. medical_professional)")
@click.option("--estimate", is_flag=True, default=False, help="Estimate cost without running API calls")
@click.option("--avg-tokens", "avg_tokens", default=800, type=int, help="Avg tokens per turn for cost estimation (default: 800)")
@click.option("--yes", "-y", "skip_confirm", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option("--cost-csv", "cost_csv", default=None, type=click.Path(), help="Export per-scenario cost data to CSV")
def scan(model, judge_model, domain, domains, run_all, report, output, verbose, delay, persona, memory, profile,
         estimate, avg_tokens, skip_confirm, cost_csv):
    """Run scenarios against a model and score behavioral safety."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.table import Table

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.engine.driver import run_scenario
    from sapien_score.scenarios.loader import load_scenario_directory
    from sapien_score.scoring.health import calculate_health_score

    console = Console()

    # --- Resolve persona/memory from profile ---
    persona_text = persona
    memory_text = memory
    if profile:
        from sapien_score.personas.loader import load_persona_profile
        try:
            prof = load_persona_profile(profile)
            if not persona_text:
                persona_text = prof.persona_text
            if not memory_text:
                memory_text = prof.memory_text
            console.print(f"[dim]Loaded profile: {prof.name} ({prof.role})[/dim]")
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    # --- Resolve domain filter ---
    domain_filter: Optional[str] = None
    domain_set: Optional[set] = None

    if domain:
        domain_filter = domain
    elif domains:
        domain_set = {d.strip() for d in domains.split(",")}

    # --- Load scenarios ---
    scenarios_dir = get_scenarios_dir()
    all_scenarios = load_scenario_directory(str(scenarios_dir), domain=domain_filter)

    if domain_set:
        all_scenarios = [s for s in all_scenarios if s.domain in domain_set]

    if not run_all and not domain and not domains:
        console.print(
            "[yellow]No filter specified. Use --all to run every built-in scenario, "
            "or --domain / --domains to narrow the set.[/yellow]"
        )
        raise SystemExit(1)

    if not all_scenarios:
        console.print(f"[red]No scenarios found matching the given filters.[/red]")
        raise SystemExit(1)

    # --- Pre-run cost estimation ---
    if estimate:
        _show_cost_estimate(console, model, all_scenarios, avg_tokens, judge_model)
        return

    # --- Build adapter ---
    adapter = get_adapter(model=model, rate_limit_delay=delay)

    # --- Build judge (Layer 2) ---
    judge = None
    if judge_model:
        from sapien_score.scoring.judge import JudgeScorer
        judge_adapter = get_adapter(model=judge_model, rate_limit_delay=delay)
        judge = JudgeScorer(adapter=judge_adapter)

    # --- Header ---
    scoring_label = "Layer 1 (deterministic)"
    if judge:
        judge_short = judge_model.split("/")[-1] if "/" in judge_model else judge_model
        scoring_label = (
            f"Layer 1 + Layer 2 (LLM judge: {judge_short})"
        )

    console.print()
    console.print(Panel.fit(
        f"[bold]SAPIEN Behavioral Safety Scan[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Scenarios: {len(all_scenarios)}\n"
        f"Scoring: {scoring_label}",
        border_style="blue",
    ))
    if judge and verbose:
        console.print(
            f"[dim]Layer 2 (LLM judge: {judge_model}) active — "
            f"dimension scores are blended 40% deterministic + 60% semantic[/dim]"
        )
    console.print()

    # --- Run with progress ---
    results = []
    running_tokens = 0
    running_cost = 0.0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning...", total=len(all_scenarios))

        for idx, scenario in enumerate(all_scenarios, 1):
            progress.update(
                task,
                description=f"[{idx}/{len(all_scenarios)}] {scenario.domain}: {scenario.title}",
            )

            result = run_scenario(
                scenario=scenario,
                adapter=adapter,
                verbose=verbose,
                judge=judge,
                persona_text=persona_text,
                memory_text=memory_text,
            )
            results.append((scenario, result))

            # Running cost display in verbose mode
            running_tokens += result.total_tokens
            running_cost += result.total_cost_usd
            if verbose and result.total_tokens > 0:
                console.print(
                    f"  [dim]Scenario complete: {result.total_tokens:,} tokens "
                    f"(${result.total_cost_usd:.4f}) | Running total: "
                    f"{running_tokens:,} tokens (${running_cost:.4f})[/dim]"
                )

            progress.advance(task)

    # --- Per-turn detail (verbose) ---
    if verbose:
        console.print()
        for scenario, result in results:
            console.print(f"\n[bold]{scenario.id}[/bold] — {scenario.title}")
            turn_table = Table(show_header=True, header_style="bold")
            turn_table.add_column("Turn", justify="right", width=5)
            turn_table.add_column("Phase", width=20)
            turn_table.add_column("Drift", justify="right", width=8)
            turn_table.add_column("Health", justify="right", width=8)
            turn_table.add_column("Rating", width=12)

            for turn in result.turns:
                drift_val = turn.scores.weighted_drift
                health_val = turn.scores.health_score
                rating_val = turn.scores.rating
                turn_table.add_row(
                    str(turn.turn_number),
                    turn.phase,
                    f"[{drift_style(drift_val)}]{drift_val:.3f}[/{drift_style(drift_val)}]",
                    f"[{health_style(health_val)}]{health_val}[/{health_style(health_val)}]",
                    f"[{rating_style(rating_val)}]{rating_val}[/{rating_style(rating_val)}]",
                )
            console.print(turn_table)

    # --- Summary table ---
    console.print()
    summary_table = Table(
        title="Scenario Results",
        show_header=True,
        header_style="bold",
    )
    summary_table.add_column("Scenario", min_width=30)
    summary_table.add_column("Domain", width=14)
    summary_table.add_column("Verdict", width=12)
    summary_table.add_column("Health", justify="right", width=8)
    summary_table.add_column("Peak Turn", justify="right", width=10)
    summary_table.add_column("Pressure", width=16)

    for scenario, result in results:
        verdict_str = result.verdict.verdict.upper()
        hs = result.verdict.health_score
        summary_table.add_row(
            scenario.title,
            scenario.domain,
            verdict_str,
            f"[{health_style(hs)}]{hs}[/{health_style(hs)}]",
            str(result.verdict.peak_turn),
            result.most_effective_pressure_type or "—",
        )

    console.print(summary_table)

    # --- Aggregate stats ---
    scores = [r.verdict.health_score for _, r in results]
    verdicts = [r.verdict.verdict for _, r in results]
    mean_score = sum(scores) / len(scores) if scores else 0
    # P10 = 10th percentile of per-scenario health scores. statistics.quantiles
    # with method="inclusive" matches numpy.percentile's linear interpolation
    # (e.g. P10 of [10..100] = 19.0). It requires at least 2 data points, so
    # fall back to min(scores) for degenerate inputs.
    if len(scores) < 2:
        p10 = min(scores) if scores else 0
    else:
        p10 = quantiles(scores, n=10, method="inclusive")[0]

    # Compute per-domain averages
    domain_scores: dict[str, list[int]] = {}
    for scenario, result in results:
        domain_scores.setdefault(scenario.domain, []).append(result.verdict.health_score)
    weakest_domain = min(domain_scores, key=lambda d: sum(domain_scores[d]) / len(domain_scores[d])) if domain_scores else "—"
    weakest_domain_avg = (
        sum(domain_scores[weakest_domain]) / len(domain_scores[weakest_domain])
        if weakest_domain in domain_scores else 0
    )

    # Compute per-dimension averages across all turns
    dim_totals: dict[str, list[float]] = {}
    for _, result in results:
        for turn in result.turns:
            for dim_score in turn.scores.dimensions:
                dim_totals.setdefault(dim_score.dimension, []).append(dim_score.drift)
    dim_averages = {
        dim: sum(vals) / len(vals) for dim, vals in dim_totals.items()
    } if dim_totals else {}

    # Calculate overall health score from dimension averages
    overall_health = calculate_health_score(dim_averages)

    # --- Summary panel ---
    console.print()
    summary_lines = [
        f"[bold]SAPIEN Health Score: [{health_style(overall_health['score'])}]"
        f"{overall_health['score']}[/{health_style(overall_health['score'])}] "
        f"— [{rating_style(overall_health['rating'])}]{overall_health['rating']}"
        f"[/{rating_style(overall_health['rating'])}][/bold]",
        "",
        f"Mean per-scenario health: {mean_score:.0f}  |  P10: {p10:.0f}",
        f"Verdicts: {verdicts.count('held')} held, {verdicts.count('drifted')} drifted, "
        f"{verdicts.count('recovered')} recovered, {verdicts.count('capitulated')} capitulated",
        f"Weakest domain: {weakest_domain} ({weakest_domain_avg:.0f})",
    ]

    if dim_averages:
        weakest_dim = max(dim_averages, key=dim_averages.get)
        summary_lines.append(f"Weakest dimension: {weakest_dim} (avg {dim_averages[weakest_dim]:.2f})")

    # Cost summary
    total_tokens_all = sum(r.total_tokens for _, r in results)
    total_cost_all = sum(r.total_cost_usd for _, r in results)
    if total_tokens_all > 0:
        summary_lines.append(f"Total tokens: {total_tokens_all:,}  |  Total cost: ${total_cost_all:.4f}")

    console.print(Panel(
        "\n".join(summary_lines),
        title="Summary",
        border_style="blue",
    ))

    # --- Dimension bars ---
    if dim_averages:
        console.print()
        dim_table = Table(title="Dimension Averages", show_header=True, header_style="bold")
        dim_table.add_column("Dimension", min_width=24)
        dim_table.add_column("Avg Drift", justify="right", width=10)
        dim_table.add_column("Bar", min_width=30)

        for dim, avg in sorted(dim_averages.items()):
            bar_len = int(avg * 30)
            bar = "\u2588" * bar_len + "\u2591" * (30 - bar_len)
            style = drift_style(avg)
            dim_table.add_row(
                dim,
                f"[{style}]{avg:.3f}[/{style}]",
                f"[{style}]{bar}[/{style}]",
            )

        console.print(dim_table)

    # --- JSON output ---
    if output:
        total_tokens_all = sum(r.total_tokens for _, r in results)
        total_cost_all = sum(r.total_cost_usd for _, r in results)
        output_data = {
            "model": model,
            "framework_version": "1.1",
            "overall_health": overall_health,
            "mean_health": round(mean_score, 1),
            "p10_health": round(p10),
            "dimension_averages": {k: round(v, 4) for k, v in dim_averages.items()},
            "total_tokens": total_tokens_all,
            "total_cost_usd": round(total_cost_all, 6),
            "results": [
                {
                    "scenario_id": scenario.id,
                    "domain": scenario.domain,
                    "title": scenario.title,
                    "verdict": result.verdict.verdict,
                    "health_score": result.verdict.health_score,
                    "peak_drift": round(result.verdict.peak_drift, 4),
                    "peak_turn": result.verdict.peak_turn,
                    "dominant_dimension": result.dominant_failure_dimension,
                    "effective_pressure": result.most_effective_pressure_type,
                    "duration_seconds": result.total_duration_seconds,
                    "input_tokens": result.total_input_tokens,
                    "output_tokens": result.total_output_tokens,
                    "total_tokens": result.total_tokens,
                    "cost_usd": round(result.total_cost_usd, 6),
                }
                for scenario, result in results
            ],
        }
        with open(output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        console.print(f"\n[green]JSON results written to {output}[/green]")

    # --- CSV cost export ---
    if cost_csv:
        _write_cost_csv(cost_csv, model, results)
        console.print(f"[green]Cost CSV written to {cost_csv}[/green]")

    # --- HTML report ---
    if report:
        from sapien_score.reporting.html_report import generate_html_report
        generate_html_report(
            results=[r for _, r in results],
            model_name=model,
            output_path=report,
            judge_model=judge_model,
        )
        console.print(f"[green]HTML report written to {report}[/green]")

    console.print()


# ---------------------------------------------------------------------------
# Cost helpers (scan-only)
# ---------------------------------------------------------------------------

def _show_cost_estimate(console, model, scenarios, avg_tokens, judge_model):
    """Show estimated cost without making API calls."""
    from rich.panel import Panel

    total_turns = sum(len(s.escalations) + 1 for s in scenarios)  # +1 for opening
    total_tokens = total_turns * avg_tokens * 2  # input + output per turn

    # If judge is enabled, double the token estimate (judge calls per scored turn)
    if judge_model:
        total_tokens *= 2

    try:
        import litellm
        input_cost, output_cost = litellm.cost_per_token(
            model=model, prompt_tokens=1, completion_tokens=1,
        )
        # Estimate: half input, half output
        estimated_cost = (total_tokens / 2) * input_cost + (total_tokens / 2) * output_cost
    except Exception:
        estimated_cost = None

    console.print()
    console.print(Panel.fit(
        f"[bold]Cost Estimation[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Scenarios: {len(scenarios)}\n"
        f"Estimated turns: {total_turns}\n"
        f"Avg tokens per turn: {avg_tokens}\n"
        f"Estimated total tokens: {total_tokens:,}" +
        (f"\n[bold]Estimated cost: ${estimated_cost:.4f}[/bold]" if estimated_cost is not None
         else "\n[yellow]Cost estimate unavailable for this model[/yellow]") +
        (f"\n[dim](includes judge model: {judge_model})[/dim]" if judge_model else ""),
        border_style="blue",
    ))

    if judge_model:
        try:
            j_input_cost, j_output_cost = litellm.cost_per_token(
                model=judge_model, prompt_tokens=1, completion_tokens=1,
            )
            judge_tokens = total_turns * avg_tokens * 2
            judge_cost = (judge_tokens / 2) * j_input_cost + (judge_tokens / 2) * j_output_cost
            console.print(f"  [dim]Judge model cost: ~${judge_cost:.4f}[/dim]")
            if estimated_cost is not None:
                console.print(f"  [dim]Combined estimate: ~${estimated_cost + judge_cost:.4f}[/dim]")
        except Exception:
            pass

    console.print()


def _write_cost_csv(path, model, results):
    """Write per-scenario cost data to CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "scenario_id", "domain", "model", "input_tokens", "output_tokens",
            "total_tokens", "cost_usd", "health_score", "rating", "verdict",
        ])
        for scenario, result in results:
            writer.writerow([
                scenario.id,
                scenario.domain,
                model,
                result.total_input_tokens,
                result.total_output_tokens,
                result.total_tokens,
                f"{result.total_cost_usd:.6f}",
                result.verdict.health_score,
                result.verdict.rating,
                result.verdict.verdict,
            ])
