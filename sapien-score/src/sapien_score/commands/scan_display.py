# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Console rendering for scan results.

Owns all Rich-based display: the scan header panel, per-turn detail
tables, scenario summary table, aggregate summary panel, dimension
bars, timing summary, and cost estimation output.  Receives a
``Console`` instance from the caller — never creates its own.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._shared import drift_style, health_style, rating_style

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def render_scan_header(
    console: "Console",
    engine,
    model: str,
    judge_model: str | None,
    collection: str | None,
    verbose: bool,
) -> None:
    """Print the scan configuration panel before scenarios run."""
    from rich.panel import Panel

    from ._shared import check_cross_family_judge

    scoring_label = "Layer 1 (deterministic)"
    if engine.judge:
        # Council scoring doesn't have a single judge_model — surface the
        # council size instead so the operator sees which backend is
        # actually running.
        if getattr(engine, "scoring_mode", "single") == "council" and engine.council is not None:
            threshold = engine.layer2_threshold
            size = engine.council.size
            if threshold > 0:
                scoring_label = (
                    f"Layer 1 + Council ({size} judges, threshold: {threshold})"
                )
            else:
                scoring_label = f"Layer 1 + Council ({size} judges)"
        elif judge_model:
            judge_short = judge_model.split("/")[-1] if "/" in judge_model else judge_model
            threshold = engine.layer2_threshold
            if threshold > 0:
                scoring_label = (
                    f"Layer 1 + Layer 2 (judge: {judge_short}, threshold: {threshold})"
                )
            else:
                scoring_label = (
                    f"Layer 1 + Layer 2 (LLM judge: {judge_short})"
                )

    cross_family_warning = check_cross_family_judge(model, judge_model)

    cr_status = "disabled (--no-counter-refusals)" if engine.no_counter_refusals else (
        "enabled" if engine.model_profile.counter_refusals_enabled else "disabled"
    )
    console.print()
    console.print(Panel.fit(
        f"[bold]SAPIEN Behavioral Safety Scan[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Collection: {collection or 'sapien'}\n"
        f"Scenarios: {len(engine.scenarios)}\n"
        f"Scoring: {scoring_label}\n"
        f"Model Tier: {engine.model_profile.display_label}\n"
        f"Counter-refusals: {cr_status}",
        border_style="blue",
    ))
    if cross_family_warning:
        console.print(f"[yellow]{cross_family_warning}[/yellow]")
    if engine.judge and verbose:
        console.print(
            f"[dim]Layer 2 (LLM judge: {judge_model}) active — "
            f"dimension scores are blended 40% deterministic + 60% semantic[/dim]"
        )
    if engine.trace_writer:
        console.print(f"[dim]Trace: {engine.trace_writer.path}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Per-turn detail (verbose mode)
# ---------------------------------------------------------------------------

def render_per_turn_detail(console: "Console", results: list) -> None:
    """Print per-turn drift/health tables for each scenario."""
    from rich.table import Table

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


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def render_summary_table(console: "Console", results: list) -> None:
    """Print the scenario results table."""
    from rich.table import Table

    from sapien_score.scoring.turn_metrics import compute_turn_metrics

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
    summary_table.add_column("1st Drift", justify="right", width=10)
    summary_table.add_column("Slope", justify="right", width=8)
    summary_table.add_column("Recovery", justify="right", width=9)
    summary_table.add_column("Pressure", width=16)
    # Expectations are COMPLEMENTARY to drift — shown in their own column so
    # a HELD scenario that still failed an expectation is visible. The column
    # only carries content when at least one scenario declared expectations.
    show_expects = any(
        getattr(r, "expectation_result", None) is not None for _, r in results
    )
    if show_expects:
        summary_table.add_column("Expects", width=10)

    for scenario, result in results:
        verdict_str = result.verdict.verdict.upper()
        hs = result.verdict.health_score
        # Round to 4dp exactly like serialize_result_entry does before the
        # JSON-path computation, so console and file metrics are identical.
        metrics = compute_turn_metrics([
            round(t.scores.weighted_drift, 4) for t in result.turns if t.scores
        ])
        fdt = metrics["first_drift_turn"]
        slope = metrics["severity_slope"]
        rec = metrics["recovery_score"]
        row = [
            scenario.title,
            scenario.domain,
            verdict_str,
            f"[{health_style(hs)}]{hs}[/{health_style(hs)}]",
            str(result.verdict.peak_turn),
            str(fdt) if fdt is not None else "—",
            f"{slope:+.3f}" if slope is not None else "—",
            f"{rec:.2f}" if rec is not None else "—",
            result.most_effective_pressure_type or "—",
        ]
        if show_expects:
            exp = getattr(result, "expectation_result", None)
            if exp is None:
                row.append("—")
            elif exp.passed:
                row.append("[green]PASS[/green]")
            else:
                row.append("[red]FAIL[/red]")
        summary_table.add_row(*row)

    console.print(summary_table)


# ---------------------------------------------------------------------------
# Aggregate summary panel + dimension bars
# ---------------------------------------------------------------------------

def render_summary_panel(
    console: "Console",
    results: list,
    dim_averages: dict,
    overall_health: dict,
    mean_score: float,
    p10: float,
) -> None:
    """Print the aggregate summary panel and dimension-average bars."""
    from rich.panel import Panel
    from rich.table import Table

    verdicts = [r.verdict.verdict for _, r in results]

    # Per-domain averages
    domain_scores: dict[str, list[int]] = {}
    for scenario, result in results:
        domain_scores.setdefault(scenario.domain, []).append(result.verdict.health_score)
    weakest_domain = min(
        domain_scores,
        key=lambda d: sum(domain_scores[d]) / len(domain_scores[d]),
    ) if domain_scores else "—"
    weakest_domain_avg = (
        sum(domain_scores[weakest_domain]) / len(domain_scores[weakest_domain])
        if weakest_domain in domain_scores else 0
    )

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


# ---------------------------------------------------------------------------
# Timing summary
# ---------------------------------------------------------------------------

def render_timing_summary(
    console: "Console",
    results: list,
    scan_elapsed: float,
) -> None:
    """Print the timing summary table if timing data is available."""
    from rich.table import Table

    from .scan_output import compute_timing_summary

    timing_summary = compute_timing_summary(results, scan_elapsed)
    if not timing_summary:
        return

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


# ---------------------------------------------------------------------------
# Judge reliability (council runs only)
# ---------------------------------------------------------------------------

def render_judge_reliability(console: "Console", results: list) -> None:
    """Print the judge-reliability summary for council-scored runs.

    Compact per-seat table (votes, agreement with final verdict, FAIL
    rate) plus run-level controversy and chairman-override lines.
    Silently skips single-judge runs — reliability artifacts only exist
    when a council voted. Measurement display only; verdicts unchanged.
    """
    from rich.table import Table

    from sapien_score.reporting.judge_reliability import compute_judge_reliability

    entries = []
    for _scenario, result in results:
        council_result = getattr(result, "council_result", None)
        if council_result is not None:
            entries.append({
                "scenario_id": _scenario.id,
                "council_scoring": council_result.to_dict(),
            })
    jr = compute_judge_reliability(entries)
    if jr is None:
        return

    console.print()
    seat_table = Table(
        title="Judge Reliability (council)",
        show_header=True,
        header_style="bold",
    )
    seat_table.add_column("Seat", min_width=16)
    seat_table.add_column("Family", width=10)
    seat_table.add_column("Votes", justify="right", width=7)
    seat_table.add_column("Missing", justify="right", width=9)
    seat_table.add_column("Agree w/ Final", justify="right", width=15)
    seat_table.add_column("FAIL Rate", justify="right", width=10)

    for judge_id, s in jr["seats"].items():
        missing = s["missing_votes"]
        missing_str = f"[red]{missing}[/red]" if missing > 0 else "0"
        seat_table.add_row(
            judge_id,
            s["family"] or "—",
            f"{s['votes']}/{s['expected_votes']}",
            missing_str,
            f"{s['agreement_with_final'] * 100:.0f}%",
            f"{s['fail_rate'] * 100:.0f}%",
        )
    console.print(seat_table)

    dis = jr["disagreement"]
    chair = jr["chairman"]
    deg = jr["degraded"]
    split_str = ", ".join(f"{k}: {v}" for k, v in dis["splits"].items()) or "—"
    console.print(
        f"  Controversy rate: {dis['controversy_rate'] * 100:.0f}% "
        f"({dis['non_unanimous']}/{jr['scored_records']} non-unanimous)  |  "
        f"Splits: {split_str}"
    )
    console.print(
        f"  Chairman: {chair['adjudicated']} adjudicated, "
        f"{chair['overrides']} overrides "
        f"({chair['override_rate'] * 100:.0f}% of non-unanimous), "
        f"{chair['failed']} failed"
    )
    if any(deg.values()):
        console.print(
            f"  [yellow]Degraded rounds: {deg['council_degraded']} council_degraded, "
            f"{deg['all_judges_failed']} all_judges_failed, "
            f"{deg['even_panel_reduced']} even_panel_reduced[/yellow]"
        )


# ---------------------------------------------------------------------------
# Cost estimation (--estimate)
# ---------------------------------------------------------------------------

def show_cost_estimate(
    console: "Console",
    model: str,
    scenarios: list,
    avg_tokens: int,
    judge_model: str | None,
    scoring_mode: str = "council",
    council_size: int = 5,
) -> None:
    """Show estimated cost without making API calls."""
    from rich.panel import Panel

    total_turns = sum(len(s.escalations) + 1 for s in scenarios)  # +1 for opening
    # Target-model tokens: input + output per turn.
    target_tokens = total_turns * avg_tokens * 2

    # If a single judge is enabled, double the per-turn token cost for the
    # judge-model estimate. Council scoring is estimated separately below
    # using a flat per-scenario-per-turn rate, since per-seat prices vary
    # and a precise rollup isn't worthwhile at estimate time.
    total_tokens = target_tokens
    if scoring_mode == "single" and judge_model:
        total_tokens *= 2

    try:
        import litellm
        input_cost, output_cost = litellm.cost_per_token(
            model=model, prompt_tokens=1, completion_tokens=1,
        )
        # Estimate: half input, half output.
        estimated_cost = (
            (target_tokens / 2) * input_cost
            + (target_tokens / 2) * output_cost
        )
    except Exception:
        estimated_cost = None

    # Council cost rule-of-thumb: ~$0.007 per scenario-turn for 5 judges,
    # ~$0.004 for 3. Anchored to the cheapest-capable default council
    # roster — actual cost varies with provider pricing.
    council_cost = None
    if scoring_mode == "council":
        per_turn_rate = 0.007 if council_size == 5 else 0.004
        council_cost = total_turns * per_turn_rate

    scoring_line = (
        f"\nScoring: Council ({council_size} judges)"
        if scoring_mode == "council"
        else f"\nScoring: Single judge ({judge_model})"
        if judge_model else "\nScoring: Layer 1 only"
    )

    console.print()
    console.print(Panel.fit(
        f"[bold]Cost Estimation[/bold]\n"
        f"Model: [cyan]{model}[/cyan]"
        f"{scoring_line}\n"
        f"Scenarios: {len(scenarios)}\n"
        f"Estimated turns: {total_turns}\n"
        f"Avg tokens per turn: {avg_tokens}\n"
        f"Estimated total tokens: {total_tokens:,}" +
        (f"\n[bold]Estimated target cost: ${estimated_cost:.4f}[/bold]" if estimated_cost is not None
         else "\n[yellow]Target-model cost unavailable[/yellow]") +
        (f"\n[bold]Estimated council cost: ${council_cost:.4f}[/bold] "
         f"[dim](~${(council_cost / max(len(scenarios), 1)):.4f} per scenario)[/dim]"
         if council_cost is not None else ""),
        border_style="blue",
    ))

    if scoring_mode == "single" and judge_model:
        try:
            import litellm
            j_input_cost, j_output_cost = litellm.cost_per_token(
                model=judge_model, prompt_tokens=1, completion_tokens=1,
            )
            judge_tokens = total_turns * avg_tokens * 2
            judge_cost = (
                (judge_tokens / 2) * j_input_cost
                + (judge_tokens / 2) * j_output_cost
            )
            console.print(f"  [dim]Judge model cost: ~${judge_cost:.4f}[/dim]")
            if estimated_cost is not None:
                console.print(
                    f"  [dim]Combined estimate: ~${estimated_cost + judge_cost:.4f}[/dim]"
                )
        except Exception as exc:
            # Cost estimation is a best-effort display nicety: litellm may not
            # know this judge model's pricing (raises) or may be unimportable.
            # Don't abort the scan over a missing cost line — but log at debug
            # so the omission is diagnosable rather than silently swallowed.
            logger.debug(
                "Judge cost estimate skipped for %s: %s", judge_model, exc
            )

    if scoring_mode == "council" and estimated_cost is not None and council_cost is not None:
        console.print(
            f"  [dim]Combined estimate: ~${estimated_cost + council_cost:.4f}[/dim]"
        )

    console.print()
