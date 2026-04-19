# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Engine setup and scenario execution loop for scans.

Coordinates adapter creation, replay/trace configuration, scenario
loading, argument resolution, the per-scenario progress loop, and
post-scan finalization (output writes, HTML report, cleanup).
Does not handle CLI argument parsing (see ``scan.py``) or
console rendering (see ``scan_display.py``).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine configuration — shuttles state from setup to the run loop
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    """All state needed to execute and finalize a scan.

    Created by :func:`setup_engine`, consumed by :func:`run_scan_loop`
    and :func:`finalize_scan`.  Avoids passing 15+ loose parameters
    between functions.
    """
    adapter: object
    judge: object = None
    trace_writer: object = None
    trace_reader: object = None
    model_profile: object = None
    scenarios: list = field(default_factory=list)
    persona_text: Optional[str] = None
    memory_text: Optional[str] = None
    no_counter_refusals: bool = False
    layer2_threshold: float = 0.0
    partial_path: str = ""
    previous_payload: Optional[dict] = None
    resume_path: Optional[str] = None
    override_rules: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Override config loading
# ---------------------------------------------------------------------------

def load_risk_overrides(console: "Console", config_path: Optional[str]) -> list:
    """Load deployer override rules from YAML, if available.

    When *config_path* is None, looks for ``./sapien-config.yaml`` as a
    default. Returns an empty list when no config is found or applicable.
    """
    from pathlib import Path

    if config_path is None:
        default = Path("sapien-config.yaml")
        if not default.exists():
            return []
        config_path = str(default)

    from sapien_score.scoring.override_config import load_override_config

    try:
        rules = load_override_config(config_path)
    except (ValueError, OSError) as e:
        console.print(f"[red]Override config error: {e}[/red]")
        raise SystemExit(1)

    console.print(
        f"[dim]Loaded {len(rules)} override rule(s) from {config_path}[/dim]"
    )
    return rules


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_engine(
    *,
    model: str,
    judge_model: Optional[str],
    domain: Optional[str],
    domains: Optional[str],
    run_all: bool,
    output: Optional[str],
    verbose: bool,
    persona: Optional[str],
    memory: Optional[str],
    profile: Optional[str],
    avg_tokens: int,
    resume: Optional[str],
    retry_delay: float,
    debug: bool,
    collection: Optional[str],
    authorship: Optional[str],
    audience: Optional[str],
    scenarios_dir_override: Optional[str],
    tier_override: str,
    no_counter_refusals: bool,
    no_trace: bool,
    replay: Optional[str],
    allow_trace_during_replay: bool,
    layer2_threshold: float = 0.0,
    console: "Console",
    override_rules: Optional[list] = None,
) -> EngineConfig:
    """Resolve arguments, build adapters, load scenarios.

    Returns an :class:`EngineConfig` containing everything
    :func:`run_scan_loop` and :func:`finalize_scan` need.
    """
    from sapien_score.engine.adapter import get_adapter
    from sapien_score.scenarios.loader import load_all_scenarios

    # --- Debug mode ---
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

    # --- Persona/memory resolution ---
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

    # --- Domain filter ---
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

    # --- Resume ---
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
            return EngineConfig(adapter=None, scenarios=[])

    # --- Replay setup ---
    trace_reader = None
    if replay:
        if not allow_trace_during_replay:
            no_trace = True
        replay_path = Path(replay)
        if not replay_path.exists():
            # Fall back to package-bundled data (e.g. examples/traces/...).
            replay_clean = Path(replay)
            if replay_clean.is_absolute() or any(part == ".." for part in replay_clean.parts):
                console.print(f"[red]Error: replay path contains illegal components: {replay}[/red]")
                raise SystemExit(1)
            from importlib.resources import files
            replay_path = Path(str(files("sapien_score").joinpath(replay)))
        if not replay_path.exists():
            console.print(f"[red]Error: replay file not found: {replay}[/red]")
            raise SystemExit(1)
        from sapien_score.tracing.replay import TraceReader, ReplayAdapter
        trace_reader = TraceReader(replay_path)
        meta = trace_reader.metadata()
        if meta["target_model"] and meta["target_model"] != model:
            console.print(
                f"[red]Model mismatch: --model '{model}' but trace "
                f"recorded with '{meta['target_model']}'[/red]"
            )
            raise SystemExit(1)
        if judge_model and meta.get("judge_model") and meta["judge_model"] != judge_model:
            console.print(
                f"[red]Judge mismatch: --judge '{judge_model}' but trace "
                f"recorded with '{meta['judge_model']}'[/red]"
            )
            raise SystemExit(1)
        console.print(
            f"[dim]Replay: {replay_path} "
            f"({meta['total_entries']} entries, run {meta['run_id'][:8]}...)[/dim]"
        )

    # --- Build adapter ---
    if trace_reader:
        from sapien_score.tracing.replay import ReplayAdapter
        adapter = ReplayAdapter(trace_reader, call_kind="target_call")
    else:
        adapter = get_adapter(model=model, base_retry_delay=retry_delay)

    # --- Trace recording ---
    trace_writer = None
    if not no_trace:
        from sapien_score.tracing.trace import TraceWriter, derive_trace_path, new_run_id
        trace_path = derive_trace_path(output)
        trace_writer = TraceWriter(path=trace_path, run_id=new_run_id())
        adapter.trace_writer = trace_writer
        adapter.call_kind = "target_call"

    # --- Model tier ---
    from sapien_score.model_profiles import get_model_profile, override_profile
    if tier_override == "auto":
        model_profile = get_model_profile(model)
    else:
        model_profile = override_profile(tier_override)

    # --- Build judge ---
    judge = None
    if judge_model:
        from sapien_score.scoring.judge import JudgeScorer
        if trace_reader:
            from sapien_score.tracing.replay import ReplayAdapter
            judge_adapter = ReplayAdapter(trace_reader, call_kind="judge_call")
        else:
            judge_adapter = get_adapter(model=judge_model, base_retry_delay=retry_delay)
        if trace_writer:
            judge_adapter.trace_writer = trace_writer
            judge_adapter.call_kind = "judge_call"
        judge = JudgeScorer(adapter=judge_adapter)

    # --- Partial results path ---
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

    return EngineConfig(
        adapter=adapter,
        judge=judge,
        trace_writer=trace_writer,
        trace_reader=trace_reader,
        model_profile=model_profile,
        scenarios=all_scenarios,
        persona_text=persona_text,
        memory_text=memory_text,
        no_counter_refusals=no_counter_refusals,
        layer2_threshold=layer2_threshold,
        partial_path=partial_path,
        previous_payload=previous_payload,
        resume_path=resume,
        override_rules=override_rules or [],
    )


# ---------------------------------------------------------------------------
# Scenario loop
# ---------------------------------------------------------------------------

def run_scan_loop(
    console: "Console",
    engine: EngineConfig,
    model: str,
    verbose: bool,
    output: Optional[str],
) -> tuple[list, list, float]:
    """Execute scenarios with a progress bar, returning (results, failed, elapsed).

    Handles per-scenario error boundaries, incremental checkpoints,
    and KeyboardInterrupt recovery.
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    from sapien_score.engine.driver import run_scenario

    from .scan_output import build_output_payload, compute_aggregates, save_partial

    scan_start_time = time.monotonic()
    results: list = []
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
            task = progress.add_task("Scanning...", total=len(engine.scenarios))

            for idx, scenario in enumerate(engine.scenarios, 1):
                progress.update(
                    task,
                    description=f"[{idx}/{len(engine.scenarios)}] {scenario.domain}: {scenario.title}",
                )

                try:
                    result = run_scenario(
                        scenario=scenario,
                        adapter=engine.adapter,
                        verbose=verbose,
                        judge=engine.judge,
                        persona_text=engine.persona_text,
                        memory_text=engine.memory_text,
                        model_profile=engine.model_profile,
                        disable_counter_refusals=engine.no_counter_refusals,
                        layer2_threshold=engine.layer2_threshold,
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
                    save_partial(results, failed_scenarios, engine.partial_path, model, engine.override_rules)
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

                # Incremental checkpoint
                if output:
                    try:
                        ckpt_dim, ckpt_health, ckpt_mean, ckpt_p10 = compute_aggregates(results)
                        ckpt_payload = build_output_payload(
                            model=model,
                            results=results,
                            dim_averages=ckpt_dim,
                            overall_health=ckpt_health,
                            mean_score=ckpt_mean,
                            p10=ckpt_p10,
                            previous_payload=engine.previous_payload,
                            resume_path=engine.resume_path,
                            override_rules=engine.override_rules,
                        )
                        with open(output, "w", encoding="utf-8") as f:
                            json.dump(ckpt_payload, f, indent=2)
                    except OSError as e:
                        logger.warning("Checkpoint write failed: %s", e)

                save_partial(results, failed_scenarios, engine.partial_path, model, engine.override_rules)
                progress.advance(task)

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Scan interrupted. Saving partial results...[/yellow]"
        )
        save_partial(results, failed_scenarios, engine.partial_path, model, engine.override_rules)
        if engine.trace_writer:
            engine.trace_writer.close()
        console.print(f"[green]Partial results saved: {engine.partial_path}[/green]")
        console.print(
            f"Resume with: voigt-kampff scan --model {model} "
            f"--resume {engine.partial_path}"
        )
        raise SystemExit(0)

    scan_elapsed = time.monotonic() - scan_start_time
    return results, failed_scenarios, scan_elapsed


# ---------------------------------------------------------------------------
# Finalization (output writes, report, cleanup)
# ---------------------------------------------------------------------------

def finalize_scan(
    *,
    console: "Console",
    engine: EngineConfig,
    model: str,
    results: list,
    failed: list,
    dim_averages: dict,
    overall_health: dict,
    mean_score: float,
    p10: float,
    output: Optional[str],
    report: Optional[str],
    cost_csv: Optional[str],
    judge_model: Optional[str],
    scan_elapsed: float,
    publish: bool = False,
    publish_label: Optional[str] = None,
    publish_primary: bool = False,
    publish_url: Optional[str] = None,
) -> None:
    """Write JSON/CSV/HTML outputs, optionally publish, and clean up."""
    from .scan_output import build_output_payload, compute_timing_summary, write_cost_csv

    # --- Build output payload (needed for JSON write and/or publish) ---
    output_data = None
    if output or publish:
        output_data = build_output_payload(
            model=model,
            results=results,
            dim_averages=dim_averages,
            overall_health=overall_health,
            mean_score=mean_score,
            p10=p10,
            previous_payload=engine.previous_payload,
            resume_path=engine.resume_path,
            override_rules=engine.override_rules,
        )
        timing_summary = compute_timing_summary(results, scan_elapsed)
        if timing_summary:
            output_data["_timing"] = timing_summary

    # --- JSON output ---
    if output and output_data:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        if failed:
            console.print(
                f"\n[green]Results saved to {output} "
                f"({len(results)} completed, {len(failed)} failed)[/green]"
            )
        else:
            console.print(f"\n[green]Results saved to {output}[/green]")

    # --- CSV cost export ---
    if cost_csv:
        write_cost_csv(cost_csv, model, results)
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

    # --- Failed scenario summary / partial cleanup ---
    if failed:
        console.print(
            f"\n[yellow]WARNING: {len(failed)} scenario(s) "
            f"failed and were skipped:[/yellow]"
        )
        for fs in failed:
            console.print(f"  - {fs['id']}: {fs['error'][:80]}")
        console.print(
            f"[dim]Partial results also saved to {engine.partial_path}[/dim]"
        )
        console.print(
            f"[yellow]Resume with: voigt-kampff scan --model {model} "
            f"--resume {engine.partial_path}[/yellow]"
        )
    else:
        try:
            Path(engine.partial_path).unlink(missing_ok=True)
        except OSError:
            pass

    if engine.trace_writer:
        engine.trace_writer.close()

    # --- Publish to scoreboard ---
    if publish and output_data:
        from sapien_score.publishing.client import publish_results, resolve_judge_family
        judge_family = resolve_judge_family(judge_model, console)
        publish_results(
            console=console,
            output_data=output_data,
            judge_model=judge_model,
            judge_family=judge_family,
            run_label=publish_label,
            is_primary=publish_primary,
            publish_url=publish_url,
        )

    console.print()
