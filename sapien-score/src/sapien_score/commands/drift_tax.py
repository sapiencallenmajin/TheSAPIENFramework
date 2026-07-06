# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff drift-tax`` — correlate drift severity with token spend.

Post-hoc analysis over completed run JSON files. Makes ZERO LLM calls.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import click


def _fmt_r(value) -> str:
    return f"{value:+.3f}" if value is not None else "n/a"


def _fmt_ratio(value) -> str:
    return f"{value:.2f}x" if value is not None else "n/a"


@click.command("drift-tax")
@click.argument(
    "run_files", nargs=-1, required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--output", "output_path", default=None, type=click.Path(),
    help="Write the full analysis as JSON",
)
@click.option(
    "--csv", "csv_path", default=None, type=click.Path(),
    help="Write per-scenario metric rows as CSV",
)
def drift_tax(run_files, output_path, csv_path):
    """Correlate behavioral drift severity with token consumption and cost.

    Reads one or more completed scan result JSON files and reports, per run
    and pooled: per-turn-normalized Pearson/Spearman correlations of drift
    severity vs tokens and cost, the naive (confounded) per-scenario
    correlations with turn count partialed out, and a "drift tax" median
    split (how much more high-drift scenarios cost per turn).
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.analysis.drift_tax import (
        SMALL_N_THRESHOLD,
        analyze_runs,
        extract_scenario_metrics,
        load_run_payload,
    )

    console = Console()

    per_run = {}
    all_warnings: list[str] = []
    for path in run_files:
        label = Path(path).stem
        # De-duplicate stems across directories.
        base, i = label, 2
        while label in per_run:
            label = f"{base}#{i}"
            i += 1
        try:
            payload = load_run_payload(path)
        except (json.JSONDecodeError, OSError) as exc:
            raise click.ClickException(f"Cannot read {path}: {exc}") from exc
        extraction = extract_scenario_metrics(payload, label)
        all_warnings.extend(extraction.warnings)
        per_run[label] = extraction.metrics

    for warning in all_warnings:
        console.print(f"[yellow]WARNING[/yellow] {warning}")

    usable = {k: v for k, v in per_run.items() if v}
    empty = [k for k, v in per_run.items() if not v]
    for label in empty:
        console.print(
            f"[red]WARNING[/red] {label}: no usable scenarios "
            "(all skipped or empty) — excluded from analysis"
        )
    if not usable:
        raise click.ClickException(
            "No usable scenario data in any input file — nothing to analyze."
        )

    report = analyze_runs(usable)

    # --- Per-scenario table ---
    console.print()
    scen_table = Table(
        title="Per-scenario drift vs spend", show_header=True,
        header_style="bold",
    )
    scen_table.add_column("Run")
    scen_table.add_column("Scenario", min_width=20)
    scen_table.add_column("Turns", justify="right")
    scen_table.add_column("Drift/turn", justify="right")
    scen_table.add_column("Out tok", justify="right")
    scen_table.add_column("Tot tok", justify="right")
    scen_table.add_column("Cost USD", justify="right")
    scen_table.add_column("Tok/turn", justify="right")
    scen_table.add_column("Resp chars/turn", justify="right")
    for label, metrics in usable.items():
        for m in metrics:
            scen_table.add_row(
                label, m.scenario_id, str(m.n_turns),
                f"{m.drift_per_turn:.4f}",
                str(m.output_tokens), str(m.total_tokens),
                f"{m.cost_usd:.4f}",
                f"{m.total_tokens_per_turn:.1f}",
                f"{m.mean_response_chars:.0f}",
            )
    console.print(scen_table)

    # --- Correlations + drift tax, per run then pooled ---
    blocks = list(report["runs"].items())
    if report["pooled"] is not None:
        blocks.append(("POOLED (all runs)", report["pooled"]))

    for label, analysis in blocks:
        console.print()
        corr_table = Table(
            title=f"Correlations — {label} (n={analysis['n_scenarios']})",
            show_header=True, header_style="bold",
        )
        corr_table.add_column("Relationship", min_width=34)
        corr_table.add_column("Pearson r", justify="right")
        corr_table.add_column("Spearman rho", justify="right")
        corr_table.add_column("n", justify="right")

        ptn = analysis["per_turn_normalized"]
        rows = [
            ("drift/turn vs output tokens/turn", ptn["drift_vs_output_tokens"]),
            ("drift/turn vs total tokens/turn", ptn["drift_vs_total_tokens"]),
            ("drift/turn vs cost/turn", ptn["drift_vs_cost"]),
            ("drift/turn vs response chars/turn", ptn["drift_vs_response_chars"]),
        ]
        for name, pair in rows:
            corr_table.add_row(
                name, _fmt_r(pair["pearson"]), _fmt_r(pair["spearman"]),
                str(pair["n"]),
            )
        naive = analysis["naive_per_scenario_CONFOUNDED"]
        corr_table.add_row(
            "[dim]total drift vs total tokens (CONFOUNDED)[/dim]",
            _fmt_r(naive["drift_vs_total_tokens"]["pearson"]),
            _fmt_r(naive["drift_vs_total_tokens"]["spearman"]),
            str(naive["drift_vs_total_tokens"]["n"]),
        )
        corr_table.add_row(
            "[dim]  ... partial (turn count controlled)[/dim]",
            _fmt_r(naive["drift_vs_total_tokens_partial_turns"]),
            "-", str(naive["drift_vs_total_tokens"]["n"]),
        )
        corr_table.add_row(
            "[dim]total drift vs total cost (CONFOUNDED)[/dim]",
            _fmt_r(naive["drift_vs_cost"]["pearson"]),
            _fmt_r(naive["drift_vs_cost"]["spearman"]),
            str(naive["drift_vs_cost"]["n"]),
        )
        corr_table.add_row(
            "[dim]  ... partial (turn count controlled)[/dim]",
            _fmt_r(naive["drift_vs_cost_partial_turns"]),
            "-", str(naive["drift_vs_cost"]["n"]),
        )
        console.print(corr_table)

        if analysis["small_n_caveat"]:
            console.print(f"[yellow]{analysis['small_n_caveat']}[/yellow]")

        tax = analysis["drift_tax"]
        if tax.get("defined"):
            console.print(Panel(
                f"High-drift scenarios (n={tax['n_high']}) consumed "
                f"[bold]{_fmt_ratio(tax['token_tax_ratio'])}[/bold] tokens/turn and "
                f"[bold]{_fmt_ratio(tax['cost_tax_ratio'])}[/bold] cost/turn "
                f"vs low-drift scenarios (n={tax['n_low']}).\n"
                f"[dim]Split at median drift/turn = "
                f"{tax['median_drift_per_turn']:.4f}. High: "
                f"{tax['high_mean_tokens_per_turn']:.1f} tok/turn, "
                f"${tax['high_mean_cost_per_turn']:.5f}/turn. Low: "
                f"{tax['low_mean_tokens_per_turn']:.1f} tok/turn, "
                f"${tax['low_mean_cost_per_turn']:.5f}/turn.[/dim]",
                title=f"Drift Tax — {label}",
                border_style="blue",
            ))
        else:
            console.print(
                f"[yellow]Drift tax undefined for {label}: "
                f"{tax.get('reason')}[/yellow]"
            )

    console.print(
        f"\n[dim]Methodology: per-turn normalization is mandatory — longer "
        f"scenarios mechanically accumulate both more drift and more tokens. "
        f"Coefficients are reported with n and no p-values; n < "
        f"{SMALL_N_THRESHOLD} is flagged as noisy. Zero LLM calls made.[/dim]"
    )

    # --- JSON output ---
    if output_path:
        payload = {
            "analysis": report,
            "warnings": all_warnings,
            "scenarios": [
                {
                    "run": m.run_label,
                    "scenario_id": m.scenario_id,
                    "domain": m.domain,
                    "health_score": m.health_score,
                    "n_turns": m.n_turns,
                    "drift_total": round(m.drift_total, 6),
                    "drift_per_turn": round(m.drift_per_turn, 6),
                    "output_tokens": m.output_tokens,
                    "total_tokens": m.total_tokens,
                    "cost_usd": m.cost_usd,
                    "output_tokens_per_turn": round(m.output_tokens_per_turn, 3),
                    "total_tokens_per_turn": round(m.total_tokens_per_turn, 3),
                    "cost_per_turn": round(m.cost_per_turn, 8),
                    "mean_response_chars": round(m.mean_response_chars, 1),
                }
                for metrics in usable.values() for m in metrics
            ],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        console.print(f"[green]Analysis JSON written to {output_path}[/green]")

    # --- CSV output ---
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run", "scenario_id", "domain", "health_score", "n_turns",
                "drift_total", "drift_per_turn", "output_tokens",
                "total_tokens", "cost_usd", "output_tokens_per_turn",
                "total_tokens_per_turn", "cost_per_turn",
                "mean_response_chars",
            ])
            for metrics in usable.values():
                for m in metrics:
                    writer.writerow([
                        m.run_label, m.scenario_id, m.domain or "",
                        m.health_score if m.health_score is not None else "",
                        m.n_turns,
                        f"{m.drift_total:.6f}", f"{m.drift_per_turn:.6f}",
                        m.output_tokens, m.total_tokens,
                        f"{m.cost_usd:.6f}",
                        f"{m.output_tokens_per_turn:.3f}",
                        f"{m.total_tokens_per_turn:.3f}",
                        f"{m.cost_per_turn:.8f}",
                        f"{m.mean_response_chars:.1f}",
                    ])
        console.print(f"[green]Per-scenario CSV written to {csv_path}[/green]")
