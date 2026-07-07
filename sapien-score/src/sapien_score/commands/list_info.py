# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff list`` and ``voigt-kampff info`` — scenario discovery."""

from __future__ import annotations

import click



@click.command("list")
@click.option("--collection", type=click.Choice(["sapien", "community", "red-team", "custom", "all"]),
              default="sapien", help="Scenario collection to use")
@click.option("--tier", type=click.Choice(["high", "standard", "low"]),
              default=None, help="Filter scenarios by effective tier")
@click.option("--pack", "pack_name", default=None,
              help="Show only the scenarios in a named pack (see 'voigt-kampff packs')")
def list_scenarios(collection, tier, pack_name):
    """List all built-in scenarios."""
    from collections import Counter

    from rich.console import Console
    from rich.table import Table

    from sapien_score.scenarios.loader import load_all_scenarios

    console = Console()
    scenarios = load_all_scenarios(collection=collection)

    pack_label = ""
    if pack_name:
        from sapien_score.scenarios.packs import PackError, load_pack, resolve_pack
        try:
            manifest = load_pack(pack_name)
        except PackError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)
        resolution = resolve_pack(manifest, scenarios)
        if resolution.unresolved_members:
            console.print(
                f"[yellow]Pack '{manifest.name}': member(s) match no "
                f"scenario: {', '.join(resolution.unresolved_members)}[/yellow]"
            )
        pack_ids = set(resolution.scenario_ids)
        scenarios = [s for s in scenarios if s.id in pack_ids]
        pack_label = f", pack '{manifest.name}' v{manifest.version}"

    if tier:
        scenarios = [s for s in scenarios if tier in s.effective_against]

    if not scenarios:
        msg = f"collection '{collection}'{pack_label}"
        if tier:
            msg += f", tier '{tier}'"
        console.print(f"[yellow]No scenarios found matching {msg}.[/yellow]")
        raise SystemExit(1)

    title = f"Scenarios — {collection}{pack_label}"
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


@click.command("packs")
@click.option("--collection", type=click.Choice(["sapien", "community", "red-team", "custom", "all"]),
              default="sapien", help="Scenario collection to resolve packs against")
def packs(collection):
    """List available scenario packs and their resolved member counts."""
    from rich.console import Console
    from rich.table import Table

    from sapien_score.scenarios.loader import load_all_scenarios
    from sapien_score.scenarios.packs import PackError, list_packs, resolve_pack

    console = Console()
    try:
        manifests = list_packs()
    except PackError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if not manifests:
        console.print("[yellow]No scenario packs found.[/yellow]")
        raise SystemExit(1)

    scenarios = load_all_scenarios(collection=collection)

    table = Table(title="Scenario Packs", show_header=True, header_style="bold")
    table.add_column("Name", min_width=12)
    table.add_column("Ver", width=4)
    table.add_column("Scenarios", justify="right", width=9)
    table.add_column("Description", min_width=40)

    stale: list[tuple[str, list[str]]] = []
    for m in manifests:
        resolution = resolve_pack(m, scenarios)
        count = str(len(resolution.scenario_ids))
        if resolution.unresolved_members:
            stale.append((m.name, resolution.unresolved_members))
            count += " [red]!"
        table.add_row(m.name, m.version, count, m.description)

    console.print()
    console.print(table)

    # Loud staleness report — a pack member that no longer resolves is a
    # typo or a removed scenario, and should be fixed, not ignored.
    for name, members in stale:
        console.print(
            f"[red]Pack '{name}': member(s) match no scenario in "
            f"collection '{collection}': {', '.join(members)}[/red]"
        )
    console.print(
        f"\n[dim]{len(manifests)} pack(s), resolved against collection "
        f"'{collection}' ({len(scenarios)} scenarios).[/dim]\n"
    )


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
