# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff diff`` — compare two runs, detect regressions, gate CI.

Post-hoc analysis over two completed run JSON files. Makes ZERO LLM calls.
Design inspired by Inspect AI's eval-log comparison pattern (see
:mod:`sapien_score.analysis.run_diff`).
"""

from __future__ import annotations

import json

import click


def _fmt(value, spec: str = "+.1f") -> str:
    if value is None:
        return "n/a"
    return format(value, spec)


@click.command("diff")
@click.argument(
    "baseline_file", type=click.Path(exists=True, dir_okay=False),
)
@click.argument(
    "candidate_file", type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--output", "output_path", default=None, type=click.Path(),
    help="Write the full machine-readable diff report as JSON",
)
@click.option(
    "--fail-on", "fail_on", default="none",
    type=click.Choice(["regression", "any-change", "none"]),
    show_default=True,
    help="CI gate: exit 1 on regressions (or on any change)",
)
@click.option(
    "--min-delta", "min_delta", default=None, type=float,
    help="Noise floor in health points (0-100) below which a same-verdict "
         "health change counts as unchanged [default: 1.0]",
)
def diff(baseline_file, candidate_file, output_path, fail_on, min_delta):
    """Compare two scan result files scenario-by-scenario.

    BASELINE_FILE is the reference run; CANDIDATE_FILE is the run under
    test. Scenarios are matched by scenario_id over the intersection.
    Verdict transitions toward a worse verdict (held -> drifted, drifted ->
    capitulated, ...) are regressions; toward a better verdict,
    improvements. Same-verdict health changes count once they exceed
    --min-delta. All deltas are candidate minus baseline. Zero LLM calls.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.analysis.run_diff import (
        DEFAULT_MIN_DELTA,
        VERDICT_RANK,
        diff_runs,
        gate_exit_code,
        load_run_payload,
    )

    console = Console()
    resolved_min_delta = DEFAULT_MIN_DELTA if min_delta is None else min_delta
    if resolved_min_delta < 0:
        raise click.ClickException("--min-delta must be >= 0")

    payloads = []
    for path in (baseline_file, candidate_file):
        try:
            payloads.append(load_run_payload(path))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
            raise click.ClickException(f"Cannot read {path}: {exc}") from exc
    baseline, candidate = payloads

    report = diff_runs(baseline, candidate, min_delta=resolved_min_delta)
    summary = report["summary"]
    comp = report["comparability"]

    # --- Comparability warnings (loud, never silent) ---
    for warning in report["warnings"]:
        console.print(f"[yellow]WARNING[/yellow] {warning}")

    # --- Header panel ---
    console.print()
    console.print(Panel(
        f"baseline:  [bold]{baseline_file}[/bold] "
        f"(model={comp['baseline']['model']}, "
        f"mode={comp['baseline']['scoring_mode']})\n"
        f"candidate: [bold]{candidate_file}[/bold] "
        f"(model={comp['candidate']['model']}, "
        f"mode={comp['candidate']['scoring_mode']})\n"
        f"scenarios: {summary['n_common']} common, "
        f"{summary['n_added']} added, {summary['n_removed']} removed  |  "
        f"noise floor: {resolved_min_delta:g} health points",
        title="voigt-kampff diff",
        border_style="blue",
    ))

    # --- Verdict transition matrix ---
    matrix = report["transition_matrix"]
    verdicts = list(matrix)
    matrix_table = Table(
        title="Verdict transitions (rows: baseline, cols: candidate)",
        show_header=True, header_style="bold",
    )
    matrix_table.add_column("baseline \\ candidate")
    for cv in verdicts:
        matrix_table.add_column(cv, justify="right")
    for bv in verdicts:
        cells = []
        for cv in verdicts:
            n = matrix[bv][cv]
            if n == 0:
                cells.append("[dim]0[/dim]")
            elif VERDICT_RANK.get(cv, 0) > VERDICT_RANK.get(bv, 0):
                cells.append(f"[red]{n}[/red]")
            elif VERDICT_RANK.get(cv, 0) < VERDICT_RANK.get(bv, 0):
                cells.append(f"[green]{n}[/green]")
            else:
                cells.append(str(n))
        matrix_table.add_row(bv, *cells)
    console.print(matrix_table)

    # --- Worst regressions ---
    worst = summary["worst_regressions"]
    if worst:
        reg_table = Table(
            title=f"Regressions ({len(worst)})", show_header=True,
            header_style="bold",
        )
        reg_table.add_column("Scenario", min_width=20)
        reg_table.add_column("Domain")
        reg_table.add_column("Verdict")
        reg_table.add_column("Health", justify="right")
        reg_table.add_column("1st drift turn", justify="right")
        reg_table.add_column("Recovery", justify="right")
        for d in worst:
            v = d["verdict"]
            h = d["health"]
            tm = d["turn_metrics_delta"]
            reg_table.add_row(
                d["scenario_id"], d["domain"] or "-",
                f"[red]{v['baseline']} -> {v['candidate']}[/red]"
                if v["changed"] else v["candidate"],
                f"{_fmt(h['delta'])} ({h['baseline']} -> {h['candidate']})",
                _fmt(tm["first_drift_turn"]["delta"], "+g"),
                _fmt(tm["recovery_score"]["delta"], "+.3f"),
            )
        console.print(reg_table)

    # --- Domains ranked by net delta ---
    domains = summary["domains_by_net_delta"]
    if domains:
        dom_table = Table(
            title="Domains by net health delta (worst first)",
            show_header=True, header_style="bold",
        )
        dom_table.add_column("Domain")
        dom_table.add_column("Scenarios", justify="right")
        dom_table.add_column("Net delta", justify="right")
        dom_table.add_column("Mean delta", justify="right")
        for row in domains:
            net = row["net_health_delta"]
            style = "red" if net < 0 else ("green" if net > 0 else "dim")
            dom_table.add_row(
                row["domain"], str(row["n_scenarios"]),
                f"[{style}]{_fmt(net)}[/{style}]",
                _fmt(row["mean_health_delta"]),
            )
        console.print(dom_table)

    # --- Summary panel ---
    lines = [
        f"regressions: [red]{summary['regressions']}[/red]   "
        f"improvements: [green]{summary['improvements']}[/green]   "
        f"unchanged: {summary['unchanged']}",
        f"mean health: {_fmt(summary['mean_health']['delta'])} "
        f"({summary['mean_health']['baseline']} -> "
        f"{summary['mean_health']['candidate']})   "
        f"overall health: {_fmt(summary['overall_health']['delta'])}",
        f"common-scenario mean health delta: "
        f"{_fmt(summary['common_mean_health_delta'])}",
    ]
    reliability = summary.get("judge_reliability_delta")
    if reliability:
        lines.append(
            "judge reliability: controversy rate "
            f"{_fmt(reliability['controversy_rate']['delta'], '+.4f')}, "
            "chairman override rate "
            f"{_fmt(reliability['chairman_override_rate']['delta'], '+.4f')}"
        )
    exit_code = gate_exit_code(report, fail_on)
    verdict_line = (
        f"[red]GATE FAILED (--fail-on {fail_on})[/red]" if exit_code
        else "[green]GATE PASSED[/green]"
        if fail_on != "none" else "[dim]no gate (--fail-on none)[/dim]"
    )
    lines.append(verdict_line)
    console.print(Panel(
        "\n".join(lines), title="Run delta summary", border_style="blue",
    ))
    console.print(
        "[dim]Deltas are candidate - baseline. Verdict severity: held < "
        "recovered < drifted < capitulated. Zero LLM calls made.[/dim]"
    )

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        console.print(f"[green]Diff JSON written to {output_path}[/green]")

    if exit_code:
        raise SystemExit(exit_code)
