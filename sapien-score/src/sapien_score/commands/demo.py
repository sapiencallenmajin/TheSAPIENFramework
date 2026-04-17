# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""``voigt-kampff demo`` — single-scenario behavioral safety demo.

Runs one curated scenario against a model, producing a compact drift
report in under 2 minutes.  Designed as the launch hook: clone, set
API key, run demo, see real behavioral drift measurement.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import click

logger = logging.getLogger(__name__)

# Single curated scenario chosen for: clear drift signal on weaker
# models, universally understood financial domain, 5 escalations
# (~8 turns) for fast completion.
DEMO_SCENARIO_ID = "sapien.financial.crypto_tax_evasion.v1"


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

def _load_demo_scenario():
    """Load the demo scenario from the scenario library.

    Raises SystemExit(1) if the scenario is missing.
    """
    from sapien_score.scenarios.loader import load_all_scenarios

    all_scenarios = load_all_scenarios(collection="all")
    for s in all_scenarios:
        if s.id == DEMO_SCENARIO_ID:
            return s

    click.echo(
        f"Error: demo scenario {DEMO_SCENARIO_ID} not found in library.\n"
        f"This may indicate a package version mismatch.",
        err=True,
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _render_demo_header(console, model: str, judge_model: Optional[str], scenario_title: str) -> None:
    """Print the demo header panel."""
    from rich.panel import Panel

    scoring = "Layer 1 + Layer 2 (LLM judge)" if judge_model else "Layer 1 (deterministic)"
    console.print()
    console.print(Panel.fit(
        f"[bold]SAPIEN Behavioral Safety Demo[/bold]\n"
        f"Model: [cyan]{model}[/cyan]\n"
        f"Scoring: {scoring}\n"
        f"Scenario: {scenario_title}\n"
        f"Estimated time: ~90 seconds",
        border_style="blue",
    ))
    console.print()


def _render_demo_result(console, scenario, result) -> None:
    """Render the single-scenario demo output."""
    from ._shared import health_style

    hs = result.verdict.health_score
    console.print()
    console.print(f"  Scenario: [bold]{scenario.title}[/bold]")
    console.print(f"  Domain:   {scenario.domain}")
    console.print(f"  Verdict:  [bold]{result.verdict.verdict.upper()}[/bold]")
    console.print(
        f"  Health:   [{health_style(hs)}][bold]{hs}[/bold][/{health_style(hs)}]"
    )


def _render_demo_footer(console, trace_path: Optional[str]) -> None:
    """Print the closing footer with replay fixture and scan pointer."""
    console.print()
    console.print(
        "  To see dramatic drift (DeepSeek v3.2 on financial scenarios):"
    )
    console.print(
        "    [bold]voigt-kampff scan --replay examples/deepseek_financial_drift.trace.jsonl"
        " --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domain financial[/bold]"
    )
    console.print()
    console.print(
        "  For full scans (multi-domain, rapport delta, HTML reports):"
    )
    console.print(
        "    [bold]voigt-kampff scan --help[/bold]"
    )
    if trace_path:
        console.print()
        console.print(f"  [dim]Trace: {trace_path}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.option("--model", required=True,
              help="Model in LiteLLM format (e.g. openai/gpt-4o-mini)")
@click.option("--judge", "judge_model", default=None,
              help="LLM judge model for Layer 2 scoring (default: none, Layer 1 only)")
@click.option("--full", "full_mode", is_flag=True, default=False,
              help="Enable Layer 2 judge scoring (uses target model as judge, slower)")
@click.option("--output", default=None, type=click.Path(),
              help="Output JSON results file")
def demo(model: str, judge_model: Optional[str], full_mode: bool, output: Optional[str]) -> None:
    """Run a quick behavioral safety demo.

    Runs one curated scenario against the specified model and reports
    the drift measurement.  Layer 1 (deterministic) scoring by default.

    \b
    Use --full to enable Layer 2 LLM judge scoring (slower but richer).

    \b
    Example:
      voigt-kampff demo --model openai/gpt-4o-mini
    """
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

    from sapien_score.engine.adapter import get_adapter
    from sapien_score.engine.driver import run_scenario
    from sapien_score.model_profiles import get_model_profile

    console = Console()

    # --- Resolve judge ---
    if full_mode and not judge_model:
        judge_model = model

    # --- Load scenario ---
    scenario = _load_demo_scenario()

    # --- Header ---
    _render_demo_header(console, model, judge_model, scenario.title)

    # --- Build adapter ---
    adapter = get_adapter(model=model, rate_limit_delay=0.5, base_retry_delay=2.0)

    # --- Trace recording ---
    from sapien_score.tracing.trace import TraceWriter, derive_trace_path, new_run_id
    effective_output = output or str(Path.home() / ".sapien_score" / "demo_results.json")
    trace_path = derive_trace_path(effective_output)
    trace_writer = TraceWriter(path=trace_path, run_id=new_run_id())
    adapter.trace_writer = trace_writer
    adapter.call_kind = "target_call"

    # --- Build judge ---
    judge = None
    if judge_model:
        from sapien_score.scoring.judge import JudgeScorer
        judge_adapter = get_adapter(model=judge_model, rate_limit_delay=0.5, base_retry_delay=2.0)
        judge_adapter.trace_writer = trace_writer
        judge_adapter.call_kind = "judge_call"
        judge = JudgeScorer(adapter=judge_adapter)

    # --- Run scenario ---
    model_profile = get_model_profile(model)
    scan_start = time.monotonic()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Running: {scenario.domain}: {scenario.title}",
                total=scenario.max_turns,
            )
            result = run_scenario(
                scenario=scenario,
                adapter=adapter,
                judge=judge,
                model_profile=model_profile,
                disable_counter_refusals=True,
            )
            progress.update(task, completed=scenario.max_turns)
    except KeyboardInterrupt:
        console.print("\n[yellow]Demo interrupted.[/yellow]")
        trace_writer.close()
        raise SystemExit(0)

    scan_elapsed = time.monotonic() - scan_start

    # --- Display ---
    _render_demo_result(console, scenario, result)
    console.print(f"  [dim]Completed in {scan_elapsed:.0f}s[/dim]")

    # --- Save output ---
    if output:
        from .scan_output import build_output_payload, compute_aggregates
        results = [(scenario, result)]
        dim_avg, overall, mean, p10 = compute_aggregates(results)
        payload = build_output_payload(
            model=model, results=results, dim_averages=dim_avg,
            overall_health=overall, mean_score=mean, p10=p10,
        )
        with open(output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # --- Cleanup ---
    trace_writer.close()

    _render_demo_footer(console, str(trace_path))
