# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""``voigt-kampff list`` and ``voigt-kampff info`` — scenario discovery."""

from __future__ import annotations

import click



@click.command("list")
@click.option("--collection", type=click.Choice(["sapien", "community", "red-team", "custom", "all"]),
              default="sapien", help="Scenario collection to use")
@click.option("--tier", type=click.Choice(["high", "standard", "low"]),
              default=None, help="Filter scenarios by effective tier")
def list_scenarios(collection, tier):
    """List all built-in scenarios."""
    from collections import Counter

    from rich.console import Console
    from rich.table import Table

    from sapien_score.scenarios.loader import load_all_scenarios

    console = Console()
    scenarios = load_all_scenarios(collection=collection)

    if tier:
        scenarios = [s for s in scenarios if tier in s.effective_against]

    if not scenarios:
        msg = f"collection '{collection}'"
        if tier:
            msg += f", tier '{tier}'"
        console.print(f"[yellow]No scenarios found matching {msg}.[/yellow]")
        raise SystemExit(1)

    title = f"Scenarios — {collection}"
    if tier:
        title += f" (tier: {tier})"

    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("ID", min_width=30)
    table.add_column("Domain", width=14)
    table.add_column("Title", min_width=30)
    table.add_column("Escalations", justify="right", width=12)
    table.add_column("Tiers", width=18)

    for s in sorted(scenarios, key=lambda x: (x.domain, x.id)):
        tiers = ", ".join(s.effective_against)
        table.add_row(s.id, s.domain, s.title, str(len(s.escalations)), tiers)

    console.print()
    console.print(table)

    # Domain summary
    domain_counts = Counter(s.domain for s in scenarios)
    summary_parts = [f"{d}: {c}" for d, c in sorted(domain_counts.items())]
    tier_label = f", tier: {tier}" if tier else ""
    console.print(f"\n[dim]{len(scenarios)} scenarios total (collection: {collection}{tier_label})[/dim]")
    console.print(f"[dim]Domains — {', '.join(summary_parts)}[/dim]\n")


@click.command()
@click.argument("scenario_id")
def info(scenario_id):
    """Show detailed information about a scenario."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sapien_score.scenarios.loader import load_all_scenarios

    console = Console()
    scenarios = load_all_scenarios(collection="all")

    match = None
    for s in scenarios:
        if s.id == scenario_id:
            match = s
            break

    if not match:
        console.print(f"[red]Scenario not found: {scenario_id}[/red]")
        console.print("[dim]Run 'voigt-kampff list' to see available scenario IDs.[/dim]")
        raise SystemExit(1)

    # --- Header panel ---
    console.print()
    console.print(Panel.fit(
        f"[bold]{match.title}[/bold]\n"
        f"ID: [cyan]{match.id}[/cyan]\n"
        f"Domain: {match.domain}  |  Severity: {match.severity}  |  "
        f"Pressure: {match.pressure_type}\n"
        f"Max turns: {match.max_turns}  |  Audience: {match.audience}",
        title="Scenario Info",
        border_style="blue",
    ))

    # --- Description ---
    console.print()
    console.print(Panel(match.description, title="Description", border_style="dim"))

    # --- Opening prompt ---
    console.print()
    console.print(Panel(match.opening_prompt, title="Opening Prompt", border_style="dim"))

    # --- Escalation table ---
    if match.escalations:
        console.print()
        esc_table = Table(title="Escalations", show_header=True, header_style="bold")
        esc_table.add_column("Turn", justify="right", width=6)
        esc_table.add_column("Pressure Type", width=20)
        esc_table.add_column("Severity", justify="right", width=9)
        esc_table.add_column("Prompt", min_width=40)

        for esc in match.escalations:
            esc_table.add_row(
                str(esc.turn),
                esc.pressure_type or "—",
                str(esc.severity) if esc.severity else "—",
                esc.prompt[:120] + ("..." if len(esc.prompt) > 120 else ""),
            )
        console.print(esc_table)

    # --- Hold variants ---
    has_hold_variants = any(esc.hold_variants for esc in match.escalations)
    if has_hold_variants:
        console.print()
        hv_table = Table(title="Hold Variants", show_header=True, header_style="bold")
        hv_table.add_column("Escalation Turn", justify="right", width=16)
        hv_table.add_column("Variant #", justify="right", width=10)
        hv_table.add_column("Prompt", min_width=40)

        for esc in match.escalations:
            for i, variant in enumerate(esc.hold_variants, 1):
                hv_table.add_row(
                    str(esc.turn),
                    str(i),
                    variant[:120] + ("..." if len(variant) > 120 else ""),
                )
        console.print(hv_table)

    # --- Tags / regulatory ---
    if match.tags:
        console.print(f"\n[dim]Tags: {', '.join(match.tags)}[/dim]")
    if match.regulatory_mapping:
        console.print(f"[dim]Regulatory: {', '.join(match.regulatory_mapping)}[/dim]")

    console.print()
