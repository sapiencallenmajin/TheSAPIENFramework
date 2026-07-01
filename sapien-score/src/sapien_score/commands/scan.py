# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

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

# Sourced for the --divergence-strategy click.Choice so the four valid
# strategy names live in exactly one place (scoring/composite.py).
from sapien_score.scoring.composite import DIVERGENCE_STRATEGIES

# Sourced for the --theme click.Choice — theme names live once in
# display/themes.py. Same pattern as DIVERGENCE_STRATEGIES.
from sapien_score.display.themes import DEFAULT_THEME, THEME_NAMES

# Display-mode literals. Lifted to constants so a typo in the body
# below ("plian", "ricjh") is a NameError at import time, not a silent
# fallthrough at runtime.
DISPLAY_MODE_RICH: str = "rich"
DISPLAY_MODE_PLAIN: str = "plain"
DISPLAY_MODE_MINIMAL: str = "minimal"
DISPLAY_MODES: tuple[str, ...] = (
    DISPLAY_MODE_RICH, DISPLAY_MODE_PLAIN, DISPLAY_MODE_MINIMAL,
)
DEFAULT_DISPLAY_MODE: str = DISPLAY_MODE_RICH


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
@click.option("--force-resume", "force_resume", is_flag=True, default=False,
              help="Skip integrity validation of the --resume file. Use only for legacy "
                   "files written before checksum support; tampering is not detected.")
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
@click.option("--display", "display_mode",
              type=click.Choice(list(DISPLAY_MODES)),
              default=DEFAULT_DISPLAY_MODE, show_default=True,
              help="Display mode: rich (animated live UI with boot sequence), "
                   "plain (legacy line output, no Live UI), "
                   "minimal (progress bar only, no panels).")
@click.option("--theme",
              type=click.Choice(list(THEME_NAMES)),
              default=DEFAULT_THEME, show_default=True,
              help="Color theme for --display rich.")
@click.option("--cinematic", "cinematic", is_flag=True, default=False,
              help="Opt into the retro cinematic boot/verdict animation (Cylon "
                   "eye + decode reveal). Off by default so it never interferes "
                   "with the live scan display; auto-silences on non-TTY/CI too.")
@click.option("--divergence-strategy", "divergence_strategy",
              type=click.Choice(list(DIVERGENCE_STRATEGIES)),
              default=None,
              help="How to resolve L1/L2 disagreement >0.40 on a dimension. "
                   "strict (default): use the higher-drift value. "
                   "council: trust the judge. "
                   "layer1: legacy lenient fallback to L1. "
                   "report: pass L2 through and log per-dim deltas for review.")
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
@click.option("--publish-transcripts", "publish_transcripts", is_flag=True, default=False,
              help="Include per-turn user_message/assistant_response text in the published "
                   "payload. Off by default — scores and metadata publish, raw transcripts stay local.")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to deployer override YAML (default: ./sapien-config.yaml if present)")
@click.option("--skip-untyped", "skip_untyped", is_flag=True, default=False,
              help="Skip scenarios missing impact_tier (no-op: all scenarios have tiers post v1.4)")
@click.option("--skip-invalid", "skip_invalid", is_flag=True, default=False,
              help="Skip (don't abort on) scenario files that fail schema validation. "
                   "Skipped files are logged and listed in skipped_scenarios in the output.")
@click.option("--scenario-ids", "scenario_ids", default=None,
              help="Comma-separated scenario IDs to run; overrides --domain/--domains/--authorship/--audience filters when set")
@click.option("--scoring", "scoring_mode", type=click.Choice(["council", "single"]),
              default="council",
              help="Scoring mode: council (default) uses multiple independent judges. "
                   "single uses one judge model (requires --judge).")
@click.option("--council-size", "council_size", type=click.Choice(["3", "5"]),
              default="5",
              help="Number of council judges. Only with --scoring council.")
@click.option("--webhook", "webhook_url", type=str, default=None,
              help="URL to POST results on drift detection (fire-and-forget). "
                   "Compatible with Slack, Teams, PagerDuty, Zapier, and PSA intake URLs.")
@click.option("--webhook-threshold", "webhook_threshold",
              type=click.Choice(["moderate", "high", "critical"]),
              default="high",
              help="Minimum severity to trigger webhook (default: high). "
                   "moderate=below 80, high=below 60, critical=below 40.")
@click.option("--webhook-test", "webhook_test", is_flag=True, default=False,
              help="POST a sample drift payload to --webhook and exit. "
                   "Use to verify your endpoint before running a real scan.")
def scan(model, judge_model, domain, domains, run_all, report, output, verbose,
         delay, persona, memory, profile, estimate, avg_tokens, cost_csv, resume,
         force_resume, retry_delay, debug, collection, authorship, audience,
         scenarios_dir_override,
         tier_override, scan_mode, display_mode, theme, cinematic,
         layer2_threshold, divergence_strategy,
         allow_partial_judging,
         no_counter_refusals, no_trace,
         replay, allow_trace_during_replay, publish, publish_label, publish_primary,
         publish_url, publisher, publish_transcripts, config_path, skip_untyped,
         skip_invalid, scenario_ids, scoring_mode, council_size,
         webhook_url, webhook_threshold, webhook_test):
    """Run scenarios against a model and score behavioral safety."""
    from rich.console import Console

    # --- Webhook test mode ---
    # Synchronous POST + early exit. Validated before publisher / scoring
    # checks so a user diagnosing a 401 from their receiver doesn't have to
    # supply --publish-label or a valid judge model first.
    if webhook_test:
        if not webhook_url:
            click.echo("Error: --webhook-test requires --webhook URL.", err=True)
            raise SystemExit(1)
        from sapien_score.webhooks import send_test_payload
        ok, detail = send_test_payload(webhook_url, model=model)
        if ok:
            click.echo(f"Webhook test OK: {detail}")
            raise SystemExit(0)
        click.echo(f"Webhook test FAILED: {detail}", err=True)
        raise SystemExit(1)

    # --- Publisher env fallback ---
    publisher = publisher or os.environ.get("SAPIEN_PUBLISHER")

    # --- Publish validation ---
    if publish and not publish_label:
        click.echo("Error: --publish requires --publish-label.", err=True)
        raise SystemExit(1)

    # --- Scoring-mode validation ---
    # Single-judge mode NEEDS --judge (no default judge exists). Council
    # mode uses its own panel; passing --judge alongside --scoring council
    # is almost certainly a mistake — warn and ignore the flag so the run
    # still proceeds with the declared scoring mode.
    if scoring_mode == "single" and not judge_model:
        click.echo(
            "Error: --scoring single requires --judge MODEL. "
            "Use --scoring council (default) for the multi-judge panel.",
            err=True,
        )
        raise SystemExit(1)
    if scoring_mode == "council" and judge_model is not None:
        click.echo(
            "Warning: --judge is ignored when --scoring council. "
            "Remove --judge, or switch to --scoring single to use it.",
            err=True,
        )
        judge_model = None

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

    # --- Display mode wiring ---
    # rich   → live UI + boot sequence + event bus subscribed to display
    # plain  → no event bus, no live UI, current behavior unchanged
    # minimal → event bus subscribed to a barebones progress-only display
    #
    # The bus is only constructed for non-plain modes so the orchestration
    # layer's `if engine.event_bus is not None:` guards continue to make
    # plain mode byte-identical to pre-display behavior.
    event_bus = None
    live_display = None
    if display_mode != DISPLAY_MODE_PLAIN:
        from sapien_score.display.events import EventBus
        from sapien_score.display.live_display import LiveScanDisplay
        event_bus = EventBus()
        # Minimal mode reuses LiveScanDisplay — it already renders a
        # progress-only header when no scenario is active. A future
        # phase can split out a leaner Minimal class if needed.
        live_display = LiveScanDisplay(event_bus, theme=theme, console=console, cinematic=cinematic)

        if display_mode == DISPLAY_MODE_RICH:
            from sapien_score.__version__ import __version__
            from sapien_score.display.cinematic import play_cinematic_boot
            from sapien_score.display.themes import get_theme
            play_cinematic_boot(
                console=console,
                theme=get_theme(theme),
                version=__version__,
                scoring_mode=scoring_mode,
                council_size=int(council_size),
                no_anim=not cinematic,
            )

    # --- Override config resolution ---
    from .scan_orchestration import load_risk_overrides
    override_rules = load_risk_overrides(console, config_path)

    # --- Webhook notifier ---
    # Built before setup_engine so a misconfigured threshold / URL surfaces
    # before any API spend. Thread the notifier through the engine so the
    # scenario loop can fire alerts without re-importing webhook plumbing.
    webhook_notifier = None
    if webhook_url:
        from sapien_score.webhooks import WebhookNotifier
        webhook_notifier = WebhookNotifier(
            url=webhook_url,
            threshold=webhook_threshold,
            model=model,
            report_path=report,
        )

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
        force_resume=force_resume,
        skip_invalid=skip_invalid,
        scoring_mode=scoring_mode,
        council_size=int(council_size),
        webhook_notifier=webhook_notifier,
        divergence_strategy=divergence_strategy,
        event_bus=event_bus,
    )

    if not engine.scenarios:
        return

    if estimate:
        show_cost_estimate(
            console, model, engine.scenarios, avg_tokens, judge_model,
            scoring_mode=scoring_mode, council_size=int(council_size),
        )
        return

    if live_display is None:
        render_scan_header(console, engine, model, judge_model, collection, verbose)

    # When a live display is attached, its rich.Live context owns the
    # terminal for the duration of the loop. The legacy `Progress` bar
    # inside run_scan_loop is still rendered but to the same Console —
    # Rich serializes them safely. On exit, stop() draws the final
    # frame and yields control back so subsequent panels print cleanly.
    if live_display is not None:
        live_display.start()
    try:
        results, failed, scan_elapsed = run_scan_loop(console, engine, model, verbose, output)
    finally:
        if live_display is not None:
            live_display.stop()

    if verbose:
        render_per_turn_detail(console, results)

    render_summary_table(console, results)

    dim_averages, overall_health, mean_score, p10 = compute_aggregates(results)
    if display_mode == DISPLAY_MODE_RICH:
        from sapien_score.display.cinematic import reveal_verdict
        reveal_verdict(console, overall_health, no_anim=not cinematic)
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
        publish_transcripts=publish_transcripts,
        layer2_threshold_applied=effective_threshold,
    )
