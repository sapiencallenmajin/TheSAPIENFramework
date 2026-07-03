# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff preflight`` — fail LOUD before a paid council scan.

Council runs repeatedly half-completed silently: drained provider credits
mid-run, a dead judge seat degrading a 5-panel to 4, or a zero-scenario
resolution. This command checks all of that up front in a few seconds and
exits non-zero if anything would sink the run, so ``preflight && scan`` can
gate the expensive call.
"""
from __future__ import annotations

import json
import os
import urllib.request

import click
import litellm
from rich.console import Console

from sapien_score.engine.council_config import DEFAULT_COUNCIL
from sapien_score.scenarios.loader import load_all_scenarios

_PASS, _FAIL, _WARN = "PASS", "FAIL", "WARN"


def check_openrouter_credit() -> tuple[str, str]:
    """Return (status, detail) for the OpenRouter credit balance."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return _WARN, "OPENROUTER_API_KEY not set (needed for Meta/Google seats + GLM/Grok targets)"
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read()).get("data", {})
        remaining = data.get("total_credits", 0) - data.get("total_usage", 0)
        status = _PASS if remaining > 5 else (_WARN if remaining > 0 else _FAIL)
        return status, f"${remaining:.2f} remaining"
    except Exception as exc:  # noqa: BLE001 — best-effort external check
        return _WARN, f"could not query balance ({exc}); check manually"


def ping_seats(size: int) -> tuple[str, str]:
    """Ping each of the first ``size`` council seats; FAIL if any is dead."""
    seats = list(DEFAULT_COUNCIL)[:size]
    live, dead = 0, []
    for seat in seats:
        try:
            litellm.completion(
                model=seat.model,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
                timeout=30,
            )
            live += 1
        except Exception as exc:  # noqa: BLE001 — a dead seat is the thing we're detecting
            dead.append(f"{seat.family} ({str(exc)[:40]})")
    status = _PASS if live == len(seats) else _FAIL
    detail = f"{live}/{len(seats)} live"
    if dead:
        detail += " — DEAD: " + "; ".join(dead)
    return status, detail


def check_scenarios() -> tuple[str, str]:
    try:
        n = len(load_all_scenarios(collection="sapien"))
        return (_PASS if n > 0 else _FAIL), f"{n} scenario(s) resolved"
    except Exception as exc:  # noqa: BLE001
        return _WARN, f"could not resolve scenarios ({exc})"


@click.command()
@click.option("--model", default=None, help="Target model (informational).")
@click.option("--council-size", type=click.Choice(["3", "5"]), default="5",
              help="Number of council seats to verify live.")
@click.option("--no-seats", is_flag=True, default=False,
              help="Skip the live seat ping (no provider calls).")
def preflight(model, council_size, no_seats):
    """Verify credits, keys, live judge seats, and scenario count before a scan."""
    console = Console()
    console.print(
        f"[bold]SAPIEN council preflight[/bold] — target={model or '(none)'}, "
        f"council-size={council_size}"
    )

    checks: list[tuple[str, str, str]] = []
    checks.append(("OpenRouter credit", *check_openrouter_credit()))
    for var, label in (
        ("DEEPSEEK_API_KEY", "DeepSeek key"),
        ("MISTRAL_API_KEY", "Mistral key"),
    ):
        checks.append((label, _PASS if os.environ.get(var) else _FAIL,
                       "set" if os.environ.get(var) else "MISSING"))
    aws_ok = bool(os.environ.get("AWS_ACCESS_KEY_ID")) or os.path.exists(
        os.path.expanduser("~/.aws/credentials")
    )
    checks.append(("AWS/Bedrock (Nova seat)", _PASS if aws_ok else _FAIL,
                   "credentials present" if aws_ok else "no env creds or ~/.aws/credentials"))
    if not no_seats:
        checks.append(("Seat liveness", *ping_seats(int(council_size))))
    checks.append(("Scenario resolution", *check_scenarios()))

    mark = {_PASS: "OK  ", _FAIL: "FAIL", _WARN: "WARN"}
    colour = {_PASS: "green", _FAIL: "red", _WARN: "yellow"}
    for name, status, detail in checks:
        console.print(f"  [{colour[status]}]{mark[status]}[/{colour[status]}] {name}: {detail}")

    if any(s == _FAIL for _, s, _ in checks):
        console.print("\n[red]PREFLIGHT: FAIL[/red] — fix the FAIL gate(s) before scanning.")
        raise SystemExit(1)
    if any(s == _WARN for _, s, _ in checks):
        console.print("\n[yellow]PREFLIGHT: PASS WITH WARNINGS[/yellow] — review WARN gates.")
        return
    console.print("\n[green]PREFLIGHT: PASS[/green] — clear to launch.")
