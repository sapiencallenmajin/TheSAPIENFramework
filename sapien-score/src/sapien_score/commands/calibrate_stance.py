# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff calibrate-stance`` — audit the Module-4 stance judge.

Loads a JSON file of {"gold": [...], "predicted": [...]} stance-label pairs
(the §3 CALIBRATION track: gold is MC-auto-derived or human-audit, predicted
is the council stance-judge) and prints the reliability report — n, overall
agreement, Cohen's kappa, per-class sensitivity/specificity, confusion
matrix — plus a PASS/FAIL against the provided --kappa-min / --sensitivity-min
publish gate. Makes ZERO LLM calls; pure post-hoc analysis.

Distinct from ``calibrate`` (Rapport-Delta judge benchmarking). This is the
Module-4 hallucination-persistence stance-judge reliability gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from sapien_score.scoring.calibration import (
    STANCE_CLASSES,
    passes_threshold,
    reliability_report,
)


def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "n/a"


@click.command("calibrate-stance")
@click.option(
    "--labels", "labels_path", required=True,
    type=click.Path(exists=True, dir_okay=False),
    help='JSON with {"gold": [...], "predicted": [...]} stance labels',
)
@click.option(
    "--kappa-min", type=float, default=None,
    help="Publish-gate minimum Cohen's kappa (pre-registered post-calibration)",
)
@click.option(
    "--sensitivity-min", type=float, default=None,
    help="Publish-gate minimum per-class sensitivity (recall)",
)
@click.option(
    "--output", "output_path", default=None, type=click.Path(),
    help="Write the full reliability report as JSON",
)
def calibrate_stance(labels_path, kappa_min, sensitivity_min, output_path):
    """Compute council stance-judge reliability and the publish gate."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    data = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    gold = data.get("gold")
    predicted = data.get("predicted")
    if not isinstance(gold, list) or not isinstance(predicted, list):
        console.print(
            "[red]Labels file must contain list fields 'gold' and "
            "'predicted'.[/red]"
        )
        raise SystemExit(1)

    try:
        report = reliability_report(gold, predicted)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    console.print()
    console.print(f"[bold]Stance-judge reliability[/bold]  n={report['n']}")
    console.print(f"  Overall agreement : {_fmt(report['overall_agreement'])}")
    console.print(f"  Cohen's kappa     : {_fmt(report['cohens_kappa'])}")
    console.print()

    table = Table(title="Per-class (one-vs-rest)", show_header=True,
                  header_style="bold")
    table.add_column("Stance")
    table.add_column("Support", justify="right")
    table.add_column("Sensitivity", justify="right")
    table.add_column("Specificity", justify="right")
    for cls in STANCE_CLASSES:
        pc = report["per_class"][cls]
        table.add_row(
            cls,
            str(pc["support"]),
            _fmt(pc["sensitivity"]),
            _fmt(pc["specificity"]),
        )
    console.print(table)
    console.print()

    # Confusion matrix (gold rows x predicted cols).
    cm = Table(title="Confusion matrix (gold ↓ / predicted →)",
               show_header=True, header_style="bold")
    cm.add_column("gold \\ pred")
    for p in STANCE_CLASSES:
        cm.add_column(p, justify="right")
    for g in STANCE_CLASSES:
        cm.add_row(g, *[str(report["confusion_matrix"][g][p])
                        for p in STANCE_CLASSES])
    console.print(cm)
    console.print()

    gate_shown = False
    if kappa_min is not None and sensitivity_min is not None:
        gate_shown = True
        ok = passes_threshold(report, kappa_min, sensitivity_min)
        verdict = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        console.print(
            f"Publish gate (kappa>={kappa_min}, sensitivity>="
            f"{sensitivity_min}): {verdict}"
        )
    else:
        console.print(
            "[yellow]No publish gate evaluated — pass BOTH --kappa-min and "
            "--sensitivity-min to gate (thresholds are pre-registered "
            "post-calibration).[/yellow]"
        )

    if output_path:
        payload = dict(report)
        if gate_shown:
            payload["gate"] = {
                "kappa_min": kappa_min,
                "sensitivity_min": sensitivity_min,
                "passed": passes_threshold(report, kappa_min, sensitivity_min),
            }
        Path(output_path).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        console.print(f"[green]Report written to {output_path}[/green]")
