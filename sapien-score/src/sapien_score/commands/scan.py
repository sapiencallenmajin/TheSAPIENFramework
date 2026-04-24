# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff scan`` — thin Click command entry point.

Argument parsing lives here; all logic is delegated to:
  - :mod:`scan_orchestration` — engine setup, scenario loop, finalization
  - :mod:`scan_output` — payload building, serialization, timing
  - :mod:`scan_display` — console rendering, cost estimation
"""

from __future__ import annotations

import os

import click

# Re-exports for backwards compatibility — external callers import these names
# from ``sapien_score.commands.scan``.
from .scan_output import (  # noqa: F401
    _build_output_payload,
    _serialize_result_entry,
    compute_aggregates as _compute_aggregates,
    quantiles,
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
@click.option("--mode", "scan_mode", type=click.Choice(["quick", "standard", "deep"]),
              default=None, help="Scan mode preset: quick (fast), standard (balanced), deep (full)")
@click.option("--layer2-threshold", "layer2_threshold", type=float, default=None,
              help="Skip Layer 2 judge on turns with weighted_drift below this (0.0=always judge). "
                   "Any value >0.0 requires --allow-partial-judging.")
@click.option("--allow-partial-judging", "allow_partial_judging", is_flag=True, default=False,
              help="Opt in to partial judging (layer2_threshold>0). Results are stamped with "
                   "layer2_threshold_applied so downstream consumers know not all turns were judged.")
@click.option("--no-counter-refusals", "no_counter_refusals", is_flag=True, default=False,
              help="Disable counter-refusal injection for faster benchmark runs")
@click.option("--no-trace", "no_trace", is_flag=True, default=False,
              help="Disable JSONL trace recording of LLM calls")
@click.option("--replay", default=None,
              help="Replay from a trace JSONL file — returns recorded LLM responses instead of calling APIs")
@click.option("--allow-trace-during-replay", "allow_trace_during_replay", is_flag=True, default=False,
              help="Allow trace recording while replaying (advanced debugging)")
@click.option("--publish", "publish", is_flag=True, default=False,
              help="Publish results to the SAPIEN scoreboard after scan")
@click.option("--publish-label", "publish_label", default=None,
              help="Label for the published run (required with --publish)")
@click.option("--publish-primary", "publish_primary", is_flag=True, default=False,
              help="Mark as this model's primary benchmark run on the scoreboard")
@click.option("--publish-url", "publish_url", default=None,
              help="Override scoreboard endpoint URL")
@click.option("--publisher", "publisher", default=None,
              help="Publisher name for this run (env: SAPIEN_PUBLISHER)")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to deployer override YAML (default: ./sapien-config.yaml if present)")
@click.option("--skip-untyped", "skip_untyped", is_flag=True, default=False,
              help="Skip scenarios missing impact_tier (no-op: all scenarios have tiers post v1.4)")
@click.option("--scenario-ids", "scenario_ids", default=None,
              help="Comma-separated scenario IDs to run; overrides --domain/--domains/--authorship/--audience filters when set")
def scan(model, judge_model, domain, domains, run_all, report, output, verbose,
         delay, persona, memory, profile, estimate, avg_tokens, cost_csv, resume,
         retry_delay, debug, collection, authorship, audience, scenarios_dir_override,
         tier_override, scan_mode, layer2_threshold, allow_partial_judging,
         no_counter_refusals, no_trace,
         replay, allow_trace_during_replay, publish, publish_label, publish_primary,
         publish_url, publisher, config_path, skip_untyped, scenario_ids):
    """Run scenarios against a model and score behavioral safety."""
    from rich.console import Console

    # --- Publisher env fallback ---
    publisher = publisher or os.environ.get("SAPIEN_PUBLISHER")

    # --- Publish validation ---
    if publish and not publish_label:
        click.echo("Error: --publish requires --publish-label.", err=True)
        raise SystemExit(1)

    # --- Mode preset resolution ---
    # Mode sets defaults; explicit flags override.
    effective_threshold = 0.0  # deep: always judge
    if scan_mode == "quick":
        effective_threshold = 0.3
        no_counter_refusals = True
    elif scan_mode == "standard":
        effective_threshold = 0.15
    # --layer2-threshold overrides mode preset
    if layer2_threshold is not None:
        effective_threshold = layer2_threshold

    # Partial judging (threshold>0) means some turns are scored on Layer 1
    # alone. Require explicit opt-in so published results can't silently
    # ship with incomplete judge coverage.
    if effective_threshold > 0.0 and not allow_partial_judging:
        click.echo(
            f"Error: layer2_threshold={effective_threshold} requires "
            "--allow-partial-judging. Partial judging skips the LLM judge on "
            "low-drift turns, which weakens benchmark coverage. Re-run with "
            "the flag to opt in, or use --mode deep / --layer2-threshold 0 "
            "for full coverage.",
            err=True,
        )
        raise SystemExit(1)

    from .scan_display import (
        render_per_turn_detail,
        render_scan_header,
        render_summary_panel,
        render_summary_table,
        render_timing_summary,
        show_cost_estimate,
    )
    from .scan_orchestration import finalize_scan, run_scan_loop, setup_engine
    from .scan_output import compute_aggregates

    console = Console()

    # --- Override config resolution ---
    from .scan_orchestration import load_risk_overrides
    override_rules = load_risk_overrides(console, config_path)

    engine = setup_engine(
        model=model, judge_model=judge_model, domain=domain, domains=domains,
        run_all=run_all, output=output, verbose=verbose,
        persona=persona, memory=memory, profile=profile, avg_tokens=avg_tokens,
        resume=resume, retry_delay=retry_delay, debug=debug,
        collection=collection, authorship=authorship, audience=audience,
        scenarios_dir_override=scenarios_dir_override, tier_override=tier_override,
        no_counter_refusals=no_counter_refusals, no_trace=no_trace, replay=replay,
        allow_trace_during_replay=allow_trace_during_replay,
        layer2_threshold=effective_threshold, console=console,
        override_rules=override_rules, scenario_ids=scenario_ids,
    )

    if not engine.scenarios:
        return

    if estimate:
        show_cost_estimate(console, model, engine.scenarios, avg_tokens, judge_model)
        return

    render_scan_header(console, engine, model, judge_model, collection, verbose)

    results, failed, scan_elapsed = run_scan_loop(console, engine, model, verbose, output)

    if verbose:
        render_per_turn_detail(console, results)

    render_summary_table(console, results)

    dim_averages, overall_health, mean_score, p10 = compute_aggregates(results)
    render_summary_panel(console, results, dim_averages, overall_health, mean_score, p10)
    render_timing_summary(console, results, scan_elapsed)

    finalize_scan(
        console=console, engine=engine, model=model, results=results,
        failed=failed, dim_averages=dim_averages, overall_health=overall_health,
        mean_score=mean_score, p10=p10, output=output, report=report,
        cost_csv=cost_csv, judge_model=judge_model, scan_elapsed=scan_elapsed,
        publish=publish, publish_label=publish_label,
        publish_primary=publish_primary, publish_url=publish_url,
        publisher=publisher,
        layer2_threshold_applied=effective_threshold,
    )
