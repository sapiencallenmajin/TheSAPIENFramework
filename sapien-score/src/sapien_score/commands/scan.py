# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""``voigt-kampff scan`` — run scenarios against a model and score safety."""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from statistics import quantiles
from typing import Optional

import click

from ._shared import (
    check_cross_family_judge,
    drift_style,
    health_style,
    rating_style,
)

logger = logging.getLogger(__name__)


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
@click.option("--cost-csv", "cost_csv", default=None, type=click.Path(), help="Export per-scenario cost data to CSV")
@click.option("--resume", type=click.Path(exists=True), default=None,
              help="Resume from a partial results JSON file — skips already-completed scenarios")
@click.option("--retry-delay", "retry_delay", type=float, default=2.0,
              help="Base delay in seconds between retries on rate limit / 5xx (default: 2)")
@click.option("--debug", "-d", is_flag=True, default=False,
              help="Show detailed scoring debug output including raw judge responses")
@click.option("--collection", type=click.Choice(["sapien", "community", "red-team", "custom", "all"]),
              default="sapien", help="Scenario collection to use")
@click.option("--authorship", type=click.Choice(["human", "llm", "llm-reviewed", "hybrid"]),
              default=None, help="Filter by scenario authorship")
@click.option("--audience", type=click.Choice(["general", "benchmark"]),
              default=None, help="Filter by target audience")
@click.option("--scenarios-dir", "scenarios_dir_override", type=click.Path(exists=True),
              default=None, help="Load scenarios from a custom directory")
@click.option("--tier", "tier_override", type=click.Choice(["auto", "high", "standard", "low"]),
              default="auto", help="Model meta-awareness tier (auto = detect from model name)")
@click.option("--no-counter-refusals", "no_counter_refusals", is_flag=True, default=False,
              help="Disable counter-refusal injection for faster benchmark runs")
def scan(model, judge_model, domain, domains, run_all, report, output, verbose, delay, persona, memory, profile,
         estimate, avg_tokens, cost_csv, resume, retry_delay, debug, collection, authorship, audience,
         scenarios_dir_override, tier_override, no_counter_refusals):
    """Run scenarios against a model and score behavioral safety."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.table import Table

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.engine.driver import run_scenario
    from sapien_score.scenarios.loader import load_all_scenarios

    # --- Debug mode: surface scoring internals, suppress LiteLLM noise ---
    if debug:
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(level=logging.DEBUG, format="%(message)s")
        else:
            root.setLevel(logging.DEBUG)
        logging.getLogger("LiteLLM").setLevel(logging.ERROR)
        logging.getLogger("litellm").setLevel(logging.ERROR)
        logging.getLogger("httpx").setLevel(logging.ERROR)
        logging.getLogger("httpcore").setLevel(logging.ERROR)

    console = Console()

    # --- Resolve persona/memory from profile ---
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

    # --- Resolve domain filter ---
    domain_filter: Optional[str] = None
    domain_set: Optional[set] = None

    if domain:
        domain_filter = domain
    elif domains:
        domain_set = {d.strip() for d in domains.split(",")}

    # --- Load scenarios ---
    all_scenarios = load_all_scenarios(
        domain=domain_filter,
        collection=collection,
        authorship=authorship,
        audience=audience,
        scenarios_dir=scenarios_dir_override,
    )

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

    # --- Resume: load prior partial results and skip already-completed ---
    # Incremental writes below keep `--output` in sync after every scenario,
    # so if a run dies the same file can be fed back via `--resume` to pick
    # up where it left off.
    previous_payload: Optional[dict] = None
    if resume:
        try:
            with open(resume, "r", encoding="utf-8") as f:
                previous_payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            console.print(f"[red]Failed to load --resume file {resume}: {e}[/red]")
            raise SystemExit(1)

        completed_ids = {
            entry.get("scenario_id")
            for entry in previous_payload.get("results", [])
            if entry.get("scenario_id")
        }
        before_count = len(all_scenarios)
        all_scenarios = [s for s in all_scenarios if s.id not in completed_ids]
        skipped = before_count - len(all_scenarios)
        console.print(
            f"[dim]Resume: loaded {len(completed_ids)} completed scenario(s) from "
            f"{resume} — skipping {skipped}, running {len(all_scenarios)} remaining[/dim]"
        )
        if not all_scenarios:
            console.print(
                "[yellow]All scenarios in the resume file are already complete — "
                "nothing to do.[/yellow]"
            )
            return

    # --- Build adapter ---
    adapter = get_adapter(model=model, rate_limit_delay=delay, base_retry_delay=retry_delay)

    # --- Model tier / counter-refusal configuration ---
    from sapien_score.model_profiles import get_model_profile, override_profile
    if tier_override == "auto":
        model_profile = get_model_profile(model)
    else:
        model_profile = override_profile(tier_override)

    # --- Build judge (Layer 2) ---
    judge = None
    if judge_model:
        from sapien_score.scoring.judge import JudgeScorer
        judge_adapter = get_adapter(model=judge_model, rate_limit_delay=delay, base_retry_delay=retry_delay)
        judge = JudgeScorer(adapter=judge_adapter)

    # --- Header ---
    scoring_label = "Layer 1 (deterministic)"
    if judge:
        judge_short = judge_model.split("/")[-1] if "/" in judge_model else judge_model
        scoring_label = (
            f"Layer 1 + Layer 2 (LLM judge: {judge_short})"
        )

    # --- Cross-family judge warning ---
    cross_family_warning = check_cross_family_judge(model, judge_model)

    cr_status = "disabled (--no-counter-refusals)" if no_counter_refusals else (
        "enabled" if model_profile.counter_refusals_enabled else "disabled"
    )
    console.print()
    console.print(Panel.fit(
        f"[bold]SAPIEN Behavioral Safety Scan[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Collection: {collection or 'sapien'}\n"
        f"Scenarios: {len(all_scenarios)}\n"
        f"Scoring: {scoring_label}\n"
        f"Model Tier: {model_profile.display_label}\n"
        f"Counter-refusals: {cr_status}",
        border_style="blue",
    ))
    if cross_family_warning:
        console.print(f"[yellow]{cross_family_warning}[/yellow]")
    if judge and verbose:
        console.print(
            f"[dim]Layer 2 (LLM judge: {judge_model}) active — "
            f"dimension scores are blended 40% deterministic + 60% semantic[/dim]"
        )
    console.print()

    # --- Partial results path ---
    # Always computed so auto-save works even without --output.  When
    # --output is set the partial sits alongside it; otherwise it falls
    # back to a well-known location the user can find.
    if output:
        p = Path(output)
        partial_path = (
            str(p.with_suffix(".partial.json"))
            if p.suffix == ".json"
            else output + ".partial.json"
        )
    else:
        partial_path = str(
            Path.home() / ".sapien_score" / "last_scan.partial.json"
        )

    # --- Run with progress ---
    scan_start_time = time.monotonic()
    results = []
    failed_scenarios: list[dict] = []
    running_tokens = 0
    running_cost = 0.0

    try:
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

                # Per-scenario error boundary: the adapter already retries
                # on transient errors, so anything surfacing here is either
                # a non-retryable error (bad auth, malformed request, …) or
                # has exhausted the retry budget. Either way, log it and
                # press on — one bad scenario shouldn't kill a long run.
                try:
                    result = run_scenario(
                        scenario=scenario,
                        adapter=adapter,
                        verbose=verbose,
                        judge=judge,
                        persona_text=persona_text,
                        memory_text=memory_text,
                        model_profile=model_profile,
                        disable_counter_refusals=no_counter_refusals,
                    )
                except Exception as e:
                    logger.warning(
                        "Scenario %s failed after retries: %s — skipping",
                        scenario.id, str(e)[:150],
                    )
                    console.print(
                        f"[yellow]  Scenario {scenario.id} failed: "
                        f"{str(e)[:120]} — skipping[/yellow]"
                    )
                    failed_scenarios.append({
                        "id": scenario.id,
                        "title": scenario.title,
                        "error": str(e)[:200],
                    })
                    _save_partial(results, failed_scenarios, partial_path, model)
                    progress.advance(task)
                    continue

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

                # Incremental checkpoint: rewrite --output after each
                # successful scenario so `--resume <output>` can recover
                # from a process death without losing completed work.
                if output:
                    try:
                        ckpt_dim, ckpt_health, ckpt_mean, ckpt_p10 = _compute_aggregates(results)
                        ckpt_payload = _build_output_payload(
                            model=model,
                            results=results,
                            dim_averages=ckpt_dim,
                            overall_health=ckpt_health,
                            mean_score=ckpt_mean,
                            p10=ckpt_p10,
                            previous_payload=previous_payload,
                            resume_path=resume,
                        )
                        with open(output, "w", encoding="utf-8") as f:
                            json.dump(ckpt_payload, f, indent=2)
                    except OSError as e:
                        logger.warning("Checkpoint write failed: %s", e)

                # Auto-save partial (always, regardless of --output)
                _save_partial(results, failed_scenarios, partial_path, model)

                progress.advance(task)

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Scan interrupted. Saving partial results...[/yellow]"
        )
        _save_partial(results, failed_scenarios, partial_path, model)
        console.print(f"[green]Partial results saved: {partial_path}[/green]")
        console.print(
            f"Resume with: voigt-kampff scan --model {model} "
            f"--resume {partial_path}"
        )
        raise SystemExit(0)

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
    # dim_averages / overall_health / mean / p10 are all derived from the
    # per-scenario results; pulled into a helper so the in-loop checkpoint
    # writer above can reuse the exact same computation.
    verdicts = [r.verdict.verdict for _, r in results]
    dim_averages, overall_health, mean_score, p10 = _compute_aggregates(results)

    # Compute per-domain averages
    domain_scores: dict[str, list[int]] = {}
    for scenario, result in results:
        domain_scores.setdefault(scenario.domain, []).append(result.verdict.health_score)
    weakest_domain = min(domain_scores, key=lambda d: sum(domain_scores[d]) / len(domain_scores[d])) if domain_scores else "—"
    weakest_domain_avg = (
        sum(domain_scores[weakest_domain]) / len(domain_scores[weakest_domain])
        if weakest_domain in domain_scores else 0
    )

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
            bar = "#" * bar_len + "-" * (30 - bar_len)
            style = drift_style(avg)
            dim_table.add_row(
                dim,
                f"[{style}]{avg:.3f}[/{style}]",
                f"[{style}]{bar}[/{style}]",
            )

        console.print(dim_table)

    # --- Timing summary ---
    scan_elapsed = time.monotonic() - scan_start_time
    timing_summary = _compute_timing_summary(results, scan_elapsed)
    if timing_summary:
        console.print()
        timing_table = Table(title="Timing Summary", show_header=False)
        timing_table.add_column("Metric", min_width=30)
        timing_table.add_column("Value", justify="right", min_width=20)
        for label, value in [
            ("Average target API call", f"{timing_summary['avg_target_api_seconds']:.2f}s"),
            ("Average judge API call", f"{timing_summary['avg_judge_api_seconds']:.2f}s" if timing_summary['avg_judge_api_seconds'] > 0 else "—"),
            ("Average turn total", f"{timing_summary['avg_turn_seconds']:.2f}s"),
            ("Average scenario total", f"{timing_summary['avg_scenario_seconds']:.2f}s"),
            ("Longest single API call", f"{timing_summary['longest_api_call']['duration']:.2f}s ({timing_summary['longest_api_call']['scenario']}, turn {timing_summary['longest_api_call']['turn']}, {timing_summary['longest_api_call']['type']})"),
            ("Total scan time", f"{timing_summary['total_scan_seconds']:.1f}s"),
            ("Time spent waiting on API", f"{timing_summary['api_wait_percent']:.0f}% of total"),
        ]:
            timing_table.add_row(label, value)
        console.print(timing_table)

    # --- JSON output ---
    # Final write overwrites any in-loop checkpoint with the fully-merged
    # payload (including resume merge against previous_payload if set).
    if output:
        output_data = _build_output_payload(
            model=model,
            results=results,
            dim_averages=dim_averages,
            overall_health=overall_health,
            mean_score=mean_score,
            p10=p10,
            previous_payload=previous_payload,
            resume_path=resume,
        )
        if timing_summary:
            output_data["_timing"] = timing_summary
        with open(output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        if failed_scenarios:
            console.print(
                f"\n[green]Results saved to {output} "
                f"({len(results)} completed, {len(failed_scenarios)} failed)[/green]"
            )
        else:
            console.print(f"\n[green]Results saved to {output}[/green]")

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

    # --- Failed scenario summary / partial file cleanup ---
    if failed_scenarios:
        console.print(
            f"\n[yellow]WARNING: {len(failed_scenarios)} scenario(s) "
            f"failed and were skipped:[/yellow]"
        )
        for fs in failed_scenarios:
            console.print(f"  - {fs['id']}: {fs['error'][:80]}")
        console.print(
            f"[dim]Partial results also saved to {partial_path}[/dim]"
        )
        console.print(
            f"[yellow]Resume with: voigt-kampff scan --model {model} "
            f"--resume {partial_path}[/yellow]"
        )
    else:
        # Full success — clean up the partial checkpoint file.
        try:
            Path(partial_path).unlink(missing_ok=True)
        except OSError:
            pass

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


# ---------------------------------------------------------------------------
# Aggregate / output-payload helpers (shared between the in-loop checkpoint
# writer and the end-of-run final write).
# ---------------------------------------------------------------------------

def _compute_aggregates(results):
    """Return (dim_averages, overall_health, mean_score, p10) from results.

    ``results`` is a list of ``(scenario, result)`` tuples. The same helper
    is invoked from two places: once per scenario inside the progress loop
    (for the checkpoint write that backs ``--resume``) and once after the
    loop to populate the console summary. Keeping the math in one place
    guarantees the checkpoint and the final write never disagree.
    """
    from sapien_score.scoring.health import calculate_health_score

    scores = [r.verdict.health_score for _, r in results]
    if not scores:
        return {}, calculate_health_score({}), 0, 0

    mean_score = sum(scores) / len(scores)
    # P10 = 10th percentile of per-scenario health scores. statistics.quantiles
    # with method="inclusive" matches numpy.percentile's linear interpolation
    # (e.g. P10 of [10..100] = 19.0). It requires at least 2 data points, so
    # fall back to min(scores) for degenerate inputs.
    if len(scores) < 2:
        p10 = min(scores)
    else:
        p10 = quantiles(scores, n=10, method="inclusive")[0]

    dim_totals: dict[str, list[float]] = {}
    for _, result in results:
        for turn in result.turns:
            for dim_score in turn.scores.dimensions:
                dim_totals.setdefault(dim_score.dimension, []).append(dim_score.drift)
    dim_averages = {
        dim: sum(vals) / len(vals) for dim, vals in dim_totals.items()
    } if dim_totals else {}

    overall_health = calculate_health_score(dim_averages)
    return dim_averages, overall_health, mean_score, p10


def _serialize_result_entry(scenario, result) -> dict:
    """Flatten a (scenario, result) pair into the dict shape stored in JSON."""
    entry = {
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
        "model_tier": result.model_tier,
        "counter_refusals_injected": result.counter_refusals_injected,
        "counter_refusal_categories": result.counter_refusal_categories,
    }
    turn_list = []
    for t in result.turns:
        turn_entry = {
            "turn": t.turn_number,
            "phase": t.phase,
            "pressure_type": t.pressure_type,
            "severity": t.severity,
            "user_message": t.user_message,
            "assistant_response": t.assistant_response,
            "drift": round(t.scores.weighted_drift, 4) if t.scores else None,
            "health_score": t.scores.health_score if t.scores else None,
            "judge_reasoning": t.judge_reasoning,
        }
        if t.is_counter_refusal:
            turn_entry["is_counter_refusal"] = True
            turn_entry["counter_category"] = t.counter_category
        turn_list.append(turn_entry)
    entry["turns"] = turn_list
    entry["api_call_timings"] = [
        {
            "turn": t.turn_number,
            "call_type": t.call_type,
            "duration_seconds": t.duration_seconds,
        }
        for t in result.api_timings
    ]
    entry["per_turn_durations"] = result.per_turn_durations
    return entry


def _build_output_payload(
    model: str,
    results: list,
    dim_averages: dict,
    overall_health: dict,
    mean_score: float,
    p10: float,
    previous_payload: Optional[dict] = None,
    resume_path: Optional[str] = None,
) -> dict:
    """Build the JSON payload written to ``--output``.

    When ``previous_payload`` is provided (i.e. a ``--resume`` run), the new
    per-scenario entries are concatenated onto the prior results list and
    all scalar aggregates are recomputed over the combined set so the
    output file always reflects the full scan, not just this session.

    Dimension averages can't be exactly recomputed from JSON because per-turn
    data isn't stored — we approximate with a weighted merge by scenario
    count. Every scenario has a similar number of turns in practice, so the
    drift from a true turn-weighted average is small.
    """
    from sapien_score.scoring.health import calculate_health_score

    total_tokens_new = sum(r.total_tokens for _, r in results)
    total_cost_new = sum(r.total_cost_usd for _, r in results)
    new_entries = [_serialize_result_entry(s, r) for s, r in results]

    if previous_payload is None:
        return {
            "model": model,
            "framework_version": "1.1",
            "overall_health": overall_health,
            "mean_health": round(mean_score, 1),
            "p10_health": round(p10),
            "dimension_averages": {k: round(v, 4) for k, v in (dim_averages or {}).items()},
            "total_tokens": total_tokens_new,
            "total_cost_usd": round(total_cost_new, 6),
            "results": new_entries,
        }

    # --- Resume merge path ---
    old_entries = previous_payload.get("results", []) or []
    combined_entries = old_entries + new_entries

    combined_scores = [e["health_score"] for e in combined_entries]
    if combined_scores:
        combined_mean = sum(combined_scores) / len(combined_scores)
        if len(combined_scores) < 2:
            combined_p10 = min(combined_scores)
        else:
            combined_p10 = quantiles(combined_scores, n=10, method="inclusive")[0]
    else:
        combined_mean = 0
        combined_p10 = 0

    old_dim = previous_payload.get("dimension_averages", {}) or {}
    old_n = len(old_entries)
    new_n = len(new_entries)
    merged_dim: dict[str, float] = {}
    for k in set(old_dim) | set(dim_averages or {}):
        o = old_dim.get(k)
        n = (dim_averages or {}).get(k)
        if o is not None and n is not None and (old_n + new_n) > 0:
            merged_dim[k] = (o * old_n + n * new_n) / (old_n + new_n)
        elif o is not None:
            merged_dim[k] = o
        elif n is not None:
            merged_dim[k] = n

    merged_overall = calculate_health_score(merged_dim) if merged_dim else overall_health
    combined_tokens = (previous_payload.get("total_tokens", 0) or 0) + total_tokens_new
    combined_cost = (previous_payload.get("total_cost_usd", 0.0) or 0.0) + total_cost_new

    payload = {
        "model": model,
        "framework_version": "1.1",
        "overall_health": merged_overall,
        "mean_health": round(combined_mean, 1),
        "p10_health": round(combined_p10),
        "dimension_averages": {k: round(v, 4) for k, v in merged_dim.items()},
        "total_tokens": combined_tokens,
        "total_cost_usd": round(combined_cost, 6),
        "results": combined_entries,
    }
    if resume_path:
        payload["resumed_from"] = str(resume_path)
    return payload


def _compute_timing_summary(results, scan_elapsed: float) -> Optional[dict]:
    """Aggregate per-call timing data from all scenario results.

    Returns a dict suitable for console display and JSON ``_timing`` output,
    or None when there are no results to summarize.
    """
    if not results:
        return None

    all_target = []
    all_judge = []
    all_turn_durations = []
    scenario_durations = []
    longest = {"duration": 0.0, "scenario": "", "turn": 0, "type": "target"}

    for scenario, result in results:
        scenario_durations.append(result.total_duration_seconds)
        all_turn_durations.extend(result.per_turn_durations)

        for t in result.api_timings:
            if t.call_type == "target":
                all_target.append(t.duration_seconds)
            elif t.call_type == "judge":
                all_judge.append(t.duration_seconds)

            if t.duration_seconds > longest["duration"]:
                longest = {
                    "duration": round(t.duration_seconds, 4),
                    "scenario": scenario.id,
                    "turn": t.turn_number,
                    "type": t.call_type,
                }

    total_api_time = sum(all_target) + sum(all_judge)

    return {
        "avg_target_api_seconds": round(sum(all_target) / len(all_target), 4) if all_target else 0,
        "avg_judge_api_seconds": round(sum(all_judge) / len(all_judge), 4) if all_judge else 0,
        "avg_turn_seconds": round(sum(all_turn_durations) / len(all_turn_durations), 4) if all_turn_durations else 0,
        "avg_scenario_seconds": round(sum(scenario_durations) / len(scenario_durations), 4) if scenario_durations else 0,
        "longest_api_call": longest,
        "total_scan_seconds": round(scan_elapsed, 2),
        "total_api_wait_seconds": round(total_api_time, 2),
        "api_wait_percent": round((total_api_time / scan_elapsed) * 100, 1) if scan_elapsed > 0 else 0,
    }


def _save_partial(results, failed_scenarios, path, model):
    """Save current progress so ``--resume`` can recover after a crash.

    Called after every scenario (success or failure) and on KeyboardInterrupt.
    Uses the same per-scenario dict format as the final output so the resume
    loader doesn't need special-casing.
    """
    try:
        data = {
            "partial": True,
            "model": model,
            "completed": len(results),
            "failed": len(failed_scenarios),
            "timestamp": datetime.now().isoformat(),
            "results": [
                _serialize_result_entry(s, r) for s, r in results
            ],
            "failed_scenarios": failed_scenarios,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.warning("Could not save partial results: %s", e)
