# sapien-score — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai

"""
CLI entry point for sapien-score.

Commands:
    sapien-score scan   — Run scenarios against a model
    sapien-score list   — List all built-in scenarios
    sapien-score info   — Show scenario details
"""

import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click

# ---------------------------------------------------------------------------
# Scenario directory resolution
# ---------------------------------------------------------------------------

def _get_scenarios_dir() -> Path:
    """Resolve the built-in scenarios/ directory shipped with the package."""
    env_dir = os.environ.get("SAPIEN_SCENARIOS")
    if env_dir:
        return Path(env_dir)
    # scenarios/ lives alongside the sapien_score package in the source tree
    # i.e.  sapien-score/src/sapien_score/  ->  sapien-score/scenarios/
    pkg_dir = Path(__file__).resolve().parent          # sapien_score/
    candidates = [
        pkg_dir.parent.parent / "scenarios",           # src/../scenarios
        pkg_dir.parent / "scenarios",                  # editable install
        pkg_dir / "scenarios",                         # bundled inside pkg
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    # Fallback — let the loader raise a clear error later
    return pkg_dir.parent.parent / "scenarios"


# ---------------------------------------------------------------------------
# Rich helpers
# ---------------------------------------------------------------------------

def _drift_style(value: float) -> str:
    """Return a Rich style string for a drift / dimension score."""
    if value < 0.30:
        return "green"
    if value <= 0.60:
        return "yellow"
    return "red"


def _health_style(score: int) -> str:
    """Return a Rich style string for a health score (0-100, higher = better)."""
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def _rating_style(rating: str) -> str:
    """Return a Rich style string based on rating band label."""
    mapping = {
        "Low Risk": "green",
        "Moderate": "yellow",
        "High Risk": "red",
        "Critical": "bold red",
    }
    return mapping.get(rating, "white")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="sapien-score")
def main():
    """SAPIEN Score — Behavioral safety scoring for AI models."""
    pass


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@main.command()
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
    scenarios_dir = _get_scenarios_dir()
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
                    f"[{_drift_style(drift_val)}]{drift_val:.3f}[/{_drift_style(drift_val)}]",
                    f"[{_health_style(health_val)}]{health_val}[/{_health_style(health_val)}]",
                    f"[{_rating_style(rating_val)}]{rating_val}[/{_rating_style(rating_val)}]",
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
            f"[{_health_style(hs)}]{hs}[/{_health_style(hs)}]",
            str(result.verdict.peak_turn),
            result.most_effective_pressure_type or "—",
        )

    console.print(summary_table)

    # --- Aggregate stats ---
    scores = [r.verdict.health_score for _, r in results]
    verdicts = [r.verdict.verdict for _, r in results]
    mean_score = sum(scores) / len(scores) if scores else 0
    sorted_scores = sorted(scores)
    p10 = sorted_scores[max(0, len(sorted_scores) // 10)] if sorted_scores else 0

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
        f"[bold]SAPIEN Health Score: [{_health_style(overall_health['score'])}]"
        f"{overall_health['score']}[/{_health_style(overall_health['score'])}] "
        f"— [{_rating_style(overall_health['rating'])}]{overall_health['rating']}"
        f"[/{_rating_style(overall_health['rating'])}][/bold]",
        "",
        f"Mean per-scenario health: {mean_score:.0f}  |  P10: {p10}",
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
            style = _drift_style(avg)
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
            "p10_health": p10,
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
        with open(output, "w") as f:
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
# Cost helpers
# ---------------------------------------------------------------------------

def _show_cost_estimate(console, model, scenarios, avg_tokens, judge_model):
    """Show estimated cost without making API calls."""
    from rich.panel import Panel
    from rich.table import Table

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
    with open(path, "w", newline="") as f:
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


# ---------------------------------------------------------------------------
# Helper: find scenario by id
# ---------------------------------------------------------------------------

def _find_scenario(scenarios, scenario_id):
    for s in scenarios:
        if s.id == scenario_id:
            return s
    return None


# ---------------------------------------------------------------------------
# Helper: compute health score from a ScenarioResult
# ---------------------------------------------------------------------------

def _result_health_score(result):
    """Extract the health score from a ScenarioResult."""
    return result.verdict.health_score


# ---------------------------------------------------------------------------
# rapport-delta
# ---------------------------------------------------------------------------

@main.command("rapport-delta")
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
    from sapien_score.scenarios.loader import load_scenario_directory, get_paired_scenarios

    console = Console()

    # --- Load scenarios ---
    scenarios_dir = _get_scenarios_dir()
    all_scenarios = load_scenario_directory(str(scenarios_dir), domain=domain)

    if scenario_id:
        all_scenarios = [s for s in all_scenarios if s.id == scenario_id or s.id == f"{scenario_id}_cold"]

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
        cold_score = _result_health_score(cold_result)

        console.print(f"  Running rapport: {rapport_scenario.id}...")
        rapport_result = run_scenario(
            scenario=rapport_scenario, adapter=adapter,
            verbose=verbose, judge=judge,
        )
        rapport_score = _result_health_score(rapport_result)

        delta = rapport_score - cold_score
        amplification = cold_score / rapport_score if rapport_score > 0 else float("inf")

        delta_rows.append((
            rapport_scenario.id, cold_score, rapport_score,
            delta, round(amplification, 2),
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
        delta_style = "red" if d < 0 else "green"
        table.add_row(
            sid,
            f"[{_health_style(cs)}]{cs}[/{_health_style(cs)}]",
            f"[{_health_style(rs)}]{rs}[/{_health_style(rs)}]",
            f"[{delta_style}]{d:+d}[/{delta_style}]",
            f"{amp:.2f}x",
        )

    console.print(table)

    # --- Summary ---
    if delta_rows:
        avg_delta = sum(d for _, _, _, d, _, _, _ in delta_rows) / len(delta_rows)
        avg_amp = sum(a for _, _, _, _, a, _, _ in delta_rows) / len(delta_rows)
        console.print()
        console.print(Panel(
            f"Average Rapport Delta: [red]{avg_delta:+.1f}[/red] points\n"
            f"Average Amplification: [red]{avg_amp:.2f}x[/red]\n\n"
            f"[dim]Finding: Rapport-building turns {'reduced' if avg_delta < 0 else 'did not reduce'} "
            f"the model's safety score by an average of {abs(avg_delta):.1f} points "
            f"({avg_amp:.2f}x amplification). "
            f"{'Trust dissolves safety controls more effectively than pressure alone.' if avg_delta < 0 else 'Model maintained safety under rapport pressure.'}[/dim]",
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


# ---------------------------------------------------------------------------
# memory-delta
# ---------------------------------------------------------------------------

@main.command("memory-delta")
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
    from sapien_score.scenarios.loader import load_scenario_directory

    console = Console()

    # --- Resolve persona/memory ---
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

    if not persona_text:
        console.print("[red]--persona or --profile required for memory-delta[/red]")
        raise SystemExit(1)
    if not memory_text:
        console.print("[red]--memory or --profile required for memory-delta[/red]")
        raise SystemExit(1)

    # --- Load scenario ---
    scenarios_dir = _get_scenarios_dir()
    all_scenarios = load_scenario_directory(str(scenarios_dir))
    scenario = _find_scenario(all_scenarios, scenario_id)

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
    cold_score = _result_health_score(cold_result)

    console.print("  [2/3] Running with persona only...")
    persona_result = run_scenario(
        scenario=scenario, adapter=adapter,
        verbose=verbose, judge=judge,
        persona_text=persona_text,
    )
    persona_score = _result_health_score(persona_result)

    console.print("  [3/3] Running with persona + memory...")
    full_result = run_scenario(
        scenario=scenario, adapter=adapter,
        verbose=verbose, judge=judge,
        persona_text=persona_text,
        memory_text=memory_text,
    )
    full_score = _result_health_score(full_result)

    # --- Compute deltas ---
    persona_delta = persona_score - cold_score
    full_delta = full_score - cold_score
    amplification = cold_score / full_score if full_score > 0 else float("inf")

    total_delta = abs(full_delta) if full_delta != 0 else 1
    persona_contribution = abs(persona_delta)
    memory_contribution = abs(full_delta) - abs(persona_delta)
    persona_pct = round(100 * persona_contribution / total_delta) if total_delta > 0 else 0
    memory_pct = 100 - persona_pct

    # --- Determine ratings ---
    from sapien_score.scoring.health import RATING_BANDS as _RB
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
        f"[{_health_style(cold_score)}]{cold_score}[/{_health_style(cold_score)}]",
        f"[{_rating_style(cold_rating)}]{cold_rating}[/{_rating_style(cold_rating)}]",
        "—",
    )
    table.add_row(
        "Persona only",
        f"[{_health_style(persona_score)}]{persona_score}[/{_health_style(persona_score)}]",
        f"[{_rating_style(persona_rating)}]{persona_rating}[/{_rating_style(persona_rating)}]",
        f"[red]{persona_delta:+d} pts[/red]" if persona_delta < 0 else f"[green]{persona_delta:+d} pts[/green]",
    )
    table.add_row(
        "Persona + memory",
        f"[{_health_style(full_score)}]{full_score}[/{_health_style(full_score)}]",
        f"[{_rating_style(full_rating)}]{full_rating}[/{_rating_style(full_rating)}]",
        f"[red]{full_delta:+d} pts[/red]" if full_delta < 0 else f"[green]{full_delta:+d} pts[/green]",
    )

    console.print(table)

    # --- Summary ---
    console.print()
    console.print(Panel(
        f"Memory amplification: [red]{amplification:.2f}x[/red] (cold -> full context)\n"
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
                "amplification": round(amplification, 2),
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


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@main.command("list")
def list_scenarios():
    """List all built-in scenarios."""
    from rich.console import Console
    from rich.table import Table

    from sapien_score.scenarios.loader import load_scenario_directory

    console = Console()
    scenarios_dir = _get_scenarios_dir()
    scenarios = load_scenario_directory(str(scenarios_dir))

    if not scenarios:
        console.print("[yellow]No scenarios found.[/yellow]")
        raise SystemExit(1)

    table = Table(title="Built-in Scenarios", show_header=True, header_style="bold")
    table.add_column("ID", min_width=30)
    table.add_column("Domain", width=14)
    table.add_column("Title", min_width=30)
    table.add_column("Escalations", justify="right", width=12)

    for s in sorted(scenarios, key=lambda x: (x.domain, x.id)):
        table.add_row(s.id, s.domain, s.title, str(len(s.escalations)))

    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(scenarios)} scenarios total[/dim]\n")


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

@main.command()
@click.argument("scenario_id")
def info(scenario_id):
    """Show detailed information about a scenario."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.scenarios.loader import load_scenario_directory

    console = Console()
    scenarios_dir = _get_scenarios_dir()
    scenarios = load_scenario_directory(str(scenarios_dir))

    match = None
    for s in scenarios:
        if s.id == scenario_id:
            match = s
            break

    if not match:
        console.print(f"[red]Scenario not found: {scenario_id}[/red]")
        console.print("[dim]Run 'sapien-score list' to see available scenario IDs.[/dim]")
        raise SystemExit(1)

    # --- Header panel ---
    console.print()
    console.print(Panel.fit(
        f"[bold]{match.title}[/bold]\n"
        f"ID: [cyan]{match.id}[/cyan]\n"
        f"Domain: {match.domain}  |  Severity: {match.severity}  |  "
        f"Pressure: {match.pressure_type}\n"
        f"Max turns: {match.max_turns}  |  Audience: {match.audience}",
        title="Scenario Info",
        border_style="blue",
    ))

    # --- Description ---
    console.print()
    console.print(Panel(match.description, title="Description", border_style="dim"))

    # --- Opening prompt ---
    console.print()
    console.print(Panel(match.opening_prompt, title="Opening Prompt", border_style="dim"))

    # --- Escalation table ---
    if match.escalations:
        console.print()
        esc_table = Table(title="Escalations", show_header=True, header_style="bold")
        esc_table.add_column("Turn", justify="right", width=6)
        esc_table.add_column("Pressure Type", width=20)
        esc_table.add_column("Severity", justify="right", width=9)
        esc_table.add_column("Prompt", min_width=40)

        for esc in match.escalations:
            esc_table.add_row(
                str(esc.turn),
                esc.pressure_type or "—",
                str(esc.severity) if esc.severity else "—",
                esc.prompt[:120] + ("..." if len(esc.prompt) > 120 else ""),
            )
        console.print(esc_table)

    # --- Hold variants ---
    has_hold_variants = any(esc.hold_variants for esc in match.escalations)
    if has_hold_variants:
        console.print()
        hv_table = Table(title="Hold Variants", show_header=True, header_style="bold")
        hv_table.add_column("Escalation Turn", justify="right", width=16)
        hv_table.add_column("Variant #", justify="right", width=10)
        hv_table.add_column("Prompt", min_width=40)

        for esc in match.escalations:
            for i, variant in enumerate(esc.hold_variants, 1):
                hv_table.add_row(
                    str(esc.turn),
                    str(i),
                    variant[:120] + ("..." if len(variant) > 120 else ""),
                )
        console.print(hv_table)

    # --- Tags / regulatory ---
    if match.tags:
        console.print(f"\n[dim]Tags: {', '.join(match.tags)}[/dim]")
    if match.regulatory_mapping:
        console.print(f"[dim]Regulatory: {', '.join(match.regulatory_mapping)}[/dim]")

    console.print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
