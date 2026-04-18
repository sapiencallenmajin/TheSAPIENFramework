# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff batch`` — orchestrate multiple scans from a YAML config.

Each entry in the config's ``runs:`` list is dispatched to the existing
``scan`` command via ``Context.invoke`` so behavior, output format, and
partial-result semantics stay identical to running ``voigt-kampff scan``
directly. The batch wrapper adds: a per-run status dashboard, resume
support at the run granularity (skip whole runs whose output file
already exists), and a combined ``_summary.json`` at the end.

Parallel execution is intentionally not supported in this version —
LiteLLM rate limits trip easily when several scans hit the same provider
concurrently, and per-run progress output would interleave illegibly.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ._shared import health_style
from .scan import scan


# Status labels are also used as Rich style keys via STATUS_STYLES below.
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
STATUS_INTERRUPTED = "INTERRUPTED"

STATUS_STYLES = {
    STATUS_PENDING: "dim",
    STATUS_RUNNING: "yellow",
    STATUS_DONE: "green",
    STATUS_FAILED: "red",
    STATUS_SKIPPED: "cyan",
    STATUS_INTERRUPTED: "magenta",
}

# Run names become filenames. Restrict to a charset that can't escape
# `output_dir` via "../" or absolute-path tricks.
_VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@click.command()
@click.argument("config_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def batch(ctx: click.Context, config_file: str) -> None:
    """Run a batch of scans defined in a YAML config.

    \b
    Example config:
        output_dir: benchmark_results
        delay: 1
        resume: true
        runs:
          - name: gpt54_gemini
            model: openai/gpt-5.4
            judge: vertex_ai/gemini-2.5-flash
            domains: [financial, medical, security]
    """
    console = Console()

    config = _load_config(console, config_file)
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    delay = float(config.get("delay", 1))
    resume_flag = bool(config.get("resume", True))
    runs: list[dict[str, Any]] = config["runs"]

    # Per-run state used by the status dashboard and the final summary.
    states: list[dict[str, Any]] = [
        {
            "name": run["name"],
            "model": run["model"],
            "judge": run.get("judge"),
            "domains": list(run.get("domains", [])),
            "status": STATUS_PENDING,
            "mean_health": None,
            "n": None,
            "elapsed_seconds": None,
            "error": None,
            "output": str(output_dir / f"{run['name']}_fms.json"),
        }
        for run in runs
    ]

    # Pre-pass: mark already-complete runs as SKIPPED so the dashboard
    # reflects the work that will actually happen.
    if resume_flag:
        for state in states:
            out_path = Path(state["output"])
            if out_path.exists():
                _hydrate_from_output(state, out_path)
                state["status"] = STATUS_SKIPPED

    _print_header(console, config_file, output_dir, len(runs), delay, resume_flag)

    for idx, state in enumerate(states, 1):
        if state["status"] == STATUS_SKIPPED:
            continue

        state["status"] = STATUS_RUNNING
        _print_dashboard(console, states, active_idx=idx)

        start = time.monotonic()
        try:
            ctx.invoke(
                scan,
                model=state["model"],
                judge_model=state["judge"],
                domains=",".join(state["domains"]) if state["domains"] else None,
                output=state["output"],
                delay=delay,
                # Explicit defaults for everything else scan() expects. We
                # don't rely on Context.invoke's default-merging because the
                # set of scan options drifts over time and silent omissions
                # would be hard to debug.
                domain=None,
                run_all=not state["domains"],
                report=None,
                verbose=False,
                persona=None,
                memory=None,
                profile=None,
                estimate=False,
                avg_tokens=800,
                cost_csv=None,
                resume=None,
                retry_delay=2.0,
                debug=False,
                collection="sapien",
                authorship=None,
                audience=None,
                scenarios_dir_override=None,
                tier_override="auto",
            )
        except KeyboardInterrupt:
            # scan() catches Ctrl+C internally and re-raises as
            # SystemExit(0), so the only way this branch fires is if the
            # interrupt landed between scan calls. Either way: stop the
            # batch — the user clearly wants out, not the next run.
            state["status"] = STATUS_INTERRUPTED
            state["error"] = "interrupted by user"
            state["elapsed_seconds"] = time.monotonic() - start
            _record_partial_path(state)
            _print_dashboard(console, states, active_idx=idx)
            console.print("\n[yellow]Batch interrupted — stopping.[/yellow]")
            _print_summary(console, states)
            _write_summary_json(output_dir / "_summary.json", states, config_file)
            raise SystemExit(130)
        except SystemExit as exc:
            # scan() uses SystemExit(1) for "nothing to do" / bad inputs and
            # SystemExit(0) for a Ctrl+C it caught itself. Treat 0 as an
            # interrupt (scan would not normally early-exit here) and any
            # non-zero code as a per-run failure.
            code = exc.code if isinstance(exc.code, int) else 1
            state["elapsed_seconds"] = time.monotonic() - start
            if code == 0:
                state["status"] = STATUS_INTERRUPTED
                state["error"] = "scan exited cleanly mid-batch (likely Ctrl+C)"
                _record_partial_path(state)
                _print_dashboard(console, states, active_idx=idx)
                console.print("\n[yellow]Batch interrupted — stopping.[/yellow]")
                _print_summary(console, states)
                _write_summary_json(output_dir / "_summary.json", states, config_file)
                raise SystemExit(130)
            state["status"] = STATUS_FAILED
            state["error"] = f"scan exited with code {code}"
        except Exception as exc:
            state["status"] = STATUS_FAILED
            state["error"] = f"{type(exc).__name__}: {exc}"[:300]
            state["elapsed_seconds"] = time.monotonic() - start
            _record_partial_path(state)
        else:
            state["elapsed_seconds"] = time.monotonic() - start
            # ctx.invoke returned without exception: read the output file.
            out_path = Path(state["output"])
            if out_path.exists():
                _hydrate_from_output(state, out_path)
                state["status"] = STATUS_DONE
            else:
                # scan completed but produced no output file — most likely
                # because no scenarios matched the filters. Surface as failure
                # so the user notices.
                state["status"] = STATUS_FAILED
                state["error"] = "scan completed but no output file was written"

        _print_dashboard(console, states, active_idx=idx)

        # Inter-run pacing — scan() already paces between API calls within a
        # run; this is a courtesy gap to let provider rate-limit windows
        # reset before the next model swap. Skip if the next run is already
        # marked SKIPPED (no API calls coming).
        next_idx = idx  # 0-based index of the next run
        if next_idx < len(states) and states[next_idx]["status"] != STATUS_SKIPPED and delay > 0:
            time.sleep(delay)

    _print_summary(console, states)
    _write_summary_json(output_dir / "_summary.json", states, config_file)
    console.print(
        f"\n[green]Batch summary saved to {output_dir / '_summary.json'}[/green]\n"
    )


# ---------------------------------------------------------------------------
# Config loading / validation
# ---------------------------------------------------------------------------

def _load_config(console: Console, config_file: str) -> dict[str, Any]:
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        console.print(f"[red]Failed to load config {config_file}: {exc}[/red]")
        raise SystemExit(1)

    if not isinstance(config, dict):
        console.print(f"[red]Config root must be a mapping (got {type(config).__name__})[/red]")
        raise SystemExit(1)

    if "output_dir" not in config:
        console.print("[red]Config missing required key: output_dir[/red]")
        raise SystemExit(1)

    runs = config.get("runs")
    if not isinstance(runs, list) or not runs:
        console.print("[red]Config must define a non-empty `runs:` list[/red]")
        raise SystemExit(1)

    seen_names: set[str] = set()
    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            console.print(f"[red]Run #{i + 1} must be a mapping[/red]")
            raise SystemExit(1)
        for required in ("name", "model"):
            if not run.get(required):
                console.print(f"[red]Run #{i + 1} missing required key: {required}[/red]")
                raise SystemExit(1)
        # Run names become filenames under output_dir. Reject anything that
        # could escape the directory ("../foo") or smuggle path separators.
        if not _VALID_NAME.match(run["name"]):
            console.print(
                f"[red]Run #{i + 1} name {run['name']!r} is not a safe filename — "
                "use only letters, digits, '_', '-', '.'[/red]"
            )
            raise SystemExit(1)
        # Duplicate names would clobber each other's output files — fail loud.
        if run["name"] in seen_names:
            console.print(f"[red]Duplicate run name: {run['name']}[/red]")
            raise SystemExit(1)
        seen_names.add(run["name"])

    return config


# ---------------------------------------------------------------------------
# Output-file helpers
# ---------------------------------------------------------------------------

def _hydrate_from_output(state: dict[str, Any], out_path: Path) -> None:
    """Pull mean_health and result count out of a completed scan's JSON."""
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    state["mean_health"] = data.get("mean_health")
    results = data.get("results")
    if isinstance(results, list):
        state["n"] = len(results)


def _record_partial_path(state: dict[str, Any]) -> None:
    """Record the partial-output path on ``state`` if scan wrote one.

    scan() auto-saves a partial alongside ``--output`` after every scenario,
    so the file is already at the expected location. We just note it on the
    state object so the summary JSON can surface it.
    """
    out_path = Path(state["output"])
    expected_partial = out_path.with_suffix(".partial.json")
    if expected_partial.exists():
        state["partial_output"] = str(expected_partial)


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------

def _print_header(
    console: Console,
    config_file: str,
    output_dir: Path,
    run_count: int,
    delay: float,
    resume_flag: bool,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]SAPIEN Batch Scan[/bold]\n"
            f"Config: [cyan]{config_file}[/cyan]\n"
            f"Output dir: {output_dir}\n"
            f"Runs: {run_count}\n"
            f"Inter-call delay: {delay}s\n"
            f"Resume: {'on' if resume_flag else 'off'}",
            border_style="blue",
        )
    )
    console.print()


def _print_dashboard(
    console: Console,
    states: list[dict[str, Any]],
    active_idx: int,
) -> None:
    active = states[active_idx - 1]
    title = (
        f"Batch [{active_idx}/{len(states)}] — "
        f"[{STATUS_STYLES[active['status']]}]{active['name']} ({active['status']})"
        f"[/{STATUS_STYLES[active['status']]}]"
    )
    table = Table(title=title, show_header=True, header_style="bold", title_justify="left")
    table.add_column("#", justify="right", width=3)
    table.add_column("Name", min_width=18)
    table.add_column("Model", min_width=24)
    table.add_column("Status", width=9)
    table.add_column("Mean", justify="right", width=6)
    table.add_column("N", justify="right", width=4)
    table.add_column("Elapsed", justify="right", width=10)

    for i, state in enumerate(states, 1):
        style = STATUS_STYLES[state["status"]]
        mean = state["mean_health"]
        if mean is None:
            mean_cell = "—"
        else:
            mean_cell = f"[{health_style(int(round(mean)))}]{mean:.0f}[/{health_style(int(round(mean)))}]"
        table.add_row(
            str(i),
            state["name"],
            state["model"],
            f"[{style}]{state['status']}[/{style}]",
            mean_cell,
            str(state["n"]) if state["n"] is not None else "—",
            _format_elapsed(state["elapsed_seconds"]),
        )

    console.print()
    console.print(table)
    console.print()


def _print_summary(console: Console, states: list[dict[str, Any]]) -> None:
    table = Table(
        title="Batch Summary",
        show_header=True,
        header_style="bold",
        title_justify="left",
    )
    table.add_column("Name", min_width=18)
    table.add_column("Model", min_width=22)
    table.add_column("Judge", min_width=22)
    table.add_column("Status", width=9)
    table.add_column("Mean", justify="right", width=6)
    table.add_column("N", justify="right", width=4)
    table.add_column("Elapsed", justify="right", width=10)

    for state in states:
        style = STATUS_STYLES[state["status"]]
        mean = state["mean_health"]
        if mean is None:
            mean_cell = "—"
        else:
            mean_cell = f"[{health_style(int(round(mean)))}]{mean:.0f}[/{health_style(int(round(mean)))}]"
        table.add_row(
            state["name"],
            state["model"],
            state["judge"] or "—",
            f"[{style}]{state['status']}[/{style}]",
            mean_cell,
            str(state["n"]) if state["n"] is not None else "—",
            _format_elapsed(state["elapsed_seconds"]),
        )

    console.print()
    console.print(table)

    failures = [s for s in states if s["status"] == STATUS_FAILED]
    if failures:
        console.print()
        console.print(f"[red]{len(failures)} run(s) failed:[/red]")
        for s in failures:
            console.print(f"  - {s['name']}: {s['error']}")


def _format_elapsed(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"


# ---------------------------------------------------------------------------
# Summary JSON
# ---------------------------------------------------------------------------

def _write_summary_json(
    summary_path: Path,
    states: list[dict[str, Any]],
    config_file: str,
) -> None:
    payload = {
        "config_file": config_file,
        "generated_at": datetime.now().isoformat(),
        "totals": {
            "runs": len(states),
            "done": sum(1 for s in states if s["status"] == STATUS_DONE),
            "skipped": sum(1 for s in states if s["status"] == STATUS_SKIPPED),
            "failed": sum(1 for s in states if s["status"] == STATUS_FAILED),
        },
        "runs": [
            {
                "name": s["name"],
                "model": s["model"],
                "judge": s["judge"],
                "domains": s["domains"],
                "status": s["status"],
                "mean_health": s["mean_health"],
                "n_scenarios": s["n"],
                "elapsed_seconds": (
                    round(s["elapsed_seconds"], 2)
                    if s["elapsed_seconds"] is not None else None
                ),
                "output_file": s["output"],
                "partial_output_file": s.get("partial_output"),
                "error": s["error"],
            }
            for s in states
        ],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
