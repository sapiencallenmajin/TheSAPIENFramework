# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""``voigt-kampff publish`` — publish an ALREADY-COMPLETED run to the scoreboard.

The scan ``--publish`` flag only publishes during a live scan and does a single
POST. Full 190-scenario council runs serialize to 7-14 MB — over Vercel's
~4.5 MB serverless body limit — so a single POST fails. This command:

  * takes one or more completed run JSON files,
  * auto-backfills ``judge_reliability`` / ``turn_metrics_summary`` if absent,
  * is council-aware (reuses :func:`build_publish_payload` — scoring_mode,
    council_size, seats_min, degraded — so council runs are never mislabeled
    'single'), and
  * chunks the ``results[]`` array when needed and POSTs sequentially,
    finalizing on the last chunk.

Makes ZERO LLM calls. Post-hoc only. ``--dry-run`` makes ZERO HTTP calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click


def _post_json(url, payload, headers, timeout, fallback_url=None):
    """POST *payload* as JSON. Returns (status_code, body_dict_or_None, error_str).

    Tries *fallback_url* only on connection/timeout errors against the primary
    (mirrors the scan --publish fallback). Never follows redirects — a bearer
    token must not bounce to another host.
    """
    import httpx

    urls = [url] + ([fallback_url] if fallback_url and fallback_url != url else [])
    last_err = None
    for attempt in urls:
        try:
            resp = httpx.post(
                attempt, json=payload, headers=headers,
                timeout=timeout, follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout) as exc:
            last_err = str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 — surface any transport error
            return (None, None, str(exc))
        body = None
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON body is fine
            body = None
        return (resp.status_code, body, None)
    return (None, None, last_err or "connection failed")


def _maybe_backfill(console, output_data):
    """Backfill judge_reliability / turn_metrics_summary in place if missing.

    Pure recomputation from the run's own results — zero LLM calls. Returns a
    list of the block names that were added (for reporting).
    """
    added = []

    if "judge_reliability" not in output_data:
        from sapien_score.reporting.judge_reliability import backfill_judge_reliability
        before = "judge_reliability" in output_data
        backfill_judge_reliability(output_data)  # in place; single-judge = no-op
        if not before and "judge_reliability" in output_data:
            added.append("judge_reliability")

    if "turn_metrics_summary" not in output_data:
        from sapien_score.scoring.turn_metrics import summarize_turn_metrics
        summary = summarize_turn_metrics(output_data.get("results") or [])
        if summary:
            output_data["turn_metrics_summary"] = summary
            added.append("turn_metrics_summary")

    return added


def _check_partial(output_data):
    """Return a partial-run warning string, or None if the run is complete."""
    n_requested = output_data.get("n_requested")
    n_completed = output_data.get("n_completed")
    n_failed = output_data.get("n_failed")
    problems = []
    if isinstance(n_failed, int) and n_failed > 0:
        problems.append(f"n_failed={n_failed}")
    if (isinstance(n_completed, int) and isinstance(n_requested, int)
            and n_completed < n_requested):
        problems.append(f"n_completed={n_completed} < n_requested={n_requested}")
    return ", ".join(problems) if problems else None


@click.command("publish")
@click.argument(
    "run_files", nargs=-1, required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option("--run-label", "run_label", required=True,
              help="Human-readable label stored on runs.run_label.")
@click.option("--primary", "is_primary", is_flag=True, default=False,
              help="Mark this run as the primary/official run for the model.")
@click.option("--publisher", "publisher", default=None,
              help="Publisher name (defaults to SAPIEN_PUBLISHER env var).")
@click.option("--endpoint", "endpoint", default=None,
              help="Ingest endpoint URL (default: production ingest URL).")
@click.option("--judge-model", "judge_model", default=None,
              help="Judge model id. Leave unset for council runs so the "
                   "endpoint auto-labels 'Council (N-seat)'.")
@click.option("--judge-family", "judge_family", default=None,
              help="Judge family (e.g. OpenAI). Inferred from --judge-model "
                   "if unset. Leave unset for council runs.")
@click.option("--include-transcripts", "include_transcripts", is_flag=True,
              default=False,
              help="Include per-turn transcript text in the payload. Off by "
                   "default — scores/metadata publish, raw text stays local.")
@click.option("--chunk-size", "chunk_size", default=25, show_default=True,
              type=click.IntRange(1, 200),
              help="Scenarios per chunk when the run must be chunked.")
@click.option("--allow-partial", "allow_partial", is_flag=True, default=False,
              help="Publish anyway when the run is partial (failures / fewer "
                   "completed than requested). Off by default — partial runs "
                   "are refused.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Print the chunk plan, payload summary and backfill actions. "
                   "Makes ZERO HTTP calls.")
def publish(run_files, run_label, is_primary, publisher, endpoint, judge_model,
            judge_family, include_transcripts, chunk_size, allow_partial,
            dry_run):
    """Publish one or more COMPLETED run JSON files to the SAPIEN scoreboard.

    Council-aware (never mislabels council runs 'single'), auto-backfills
    judge_reliability / turn_metrics_summary, and chunks large runs to stay
    under the serverless body limit. Zero LLM calls; ``--dry-run`` makes zero
    HTTP calls.
    """
    from rich.console import Console

    from sapien_score.net_safety import validate_post_url
    from sapien_score.publishing.client import (
        DEFAULT_INGEST_URL,
        FALLBACK_INGEST_URL,
        build_publish_payload,
        resolve_judge_family,
    )
    from sapien_score.publishing.chunking import (
        build_chunk_payloads,
        inject_run_id,
        payload_size_bytes,
        plan_chunks,
    )

    console = Console()
    publisher = publisher or os.environ.get("SAPIEN_PUBLISHER")
    url = endpoint or os.environ.get("SAPIEN_INGEST_URL", DEFAULT_INGEST_URL)
    fallback = FALLBACK_INGEST_URL if url == DEFAULT_INGEST_URL else None

    # --- Fail-loud preconditions (before any file work) ---
    try:
        validate_post_url(url)
    except ValueError as exc:
        raise click.ClickException(f"Invalid --endpoint: {exc}")

    api_key = os.environ.get("SAPIEN_INGEST_API_KEY", "")
    if not api_key and not dry_run:
        raise click.ClickException(
            "SAPIEN_INGEST_API_KEY is not set. Export your ingest token "
            "(or use --dry-run to preview the chunk plan without publishing)."
        )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    n_files = len(run_files)
    any_failure = False
    for file_idx, run_file in enumerate(run_files, start=1):
        prefix = f"[{file_idx}/{n_files}] " if n_files > 1 else ""
        console.rule(f"{prefix}{Path(run_file).name}")

        try:
            with open(run_file, encoding="utf-8") as f:
                output_data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]Failed to read {run_file}: {exc}[/red]")
            any_failure = True
            continue

        results = output_data.get("results")
        if not isinstance(results, list) or not results:
            console.print(f"[red]Refusing to publish: {run_file} has no results[].[/red]")
            any_failure = True
            continue

        # Partial-run guard.
        partial = _check_partial(output_data)
        if partial:
            if allow_partial:
                console.print(
                    f"[yellow]Partial run ({partial}) — publishing anyway "
                    f"(--allow-partial).[/yellow]"
                )
            else:
                console.print(
                    f"[red]Refusing to publish partial run ({partial}). "
                    f"Re-run/resume to completion, or pass --allow-partial to "
                    f"publish it anyway.[/red]"
                )
                any_failure = True
                continue

        # Auto-backfill (zero LLM calls).
        added = _maybe_backfill(console, output_data)
        if added:
            console.print(f"[cyan]Backfilled: {', '.join(added)}[/cyan]")
        else:
            console.print("[dim]Backfill: judge_reliability / turn_metrics_summary "
                          "already present or not applicable.[/dim]")

        # Resolve judge_family (only when a judge_model is given — council runs
        # leave both unset so the endpoint auto-labels the panel).
        resolved_family = judge_family
        if judge_model and not resolved_family:
            resolved_family = resolve_judge_family(judge_model, console)

        # Build the full payload once (council-aware, shared with scan --publish).
        full_payload = build_publish_payload(
            output_data=output_data,
            judge_model=judge_model,
            judge_family=resolved_family,
            run_label=run_label,
            is_primary=is_primary,
            publisher=publisher,
            publish_transcripts=include_transcripts,
        )

        size_bytes = payload_size_bytes(full_payload)
        plan = plan_chunks(len(results), chunk_size, payload_bytes=size_bytes)

        # --- Summary ---
        mode = full_payload.get("scoring_mode", "single")
        cs = full_payload.get("council_size")
        console.print(
            f"scoring_mode: [bold]{mode}[/bold]"
            + (f" (council_size={cs}, seats_min={full_payload.get('council_seats_min')}, "
               f"degraded={full_payload.get('council_degraded_scenarios')})"
               if mode == "council" else "")
        )
        console.print(
            f"scenarios: {len(results)}   payload: {size_bytes / 1024 / 1024:.2f} MB   "
            f"judge_model: {judge_model or '(auto)'}   judge_family: {resolved_family or '(auto)'}"
        )
        if plan.needs_chunking:
            console.print(
                f"plan: [bold]{plan.total_chunks} chunks[/bold] of "
                f"{plan.effective_chunk_size} scenarios (trigger: {plan.reason})"
            )
            for i, (s, e) in enumerate(plan.chunk_ranges(), start=1):
                console.print(f"  chunk {i}/{plan.total_chunks}: scenarios[{s}..{e - 1}] ({e - s} items)")
        else:
            console.print(f"plan: [bold]single POST[/bold] ({len(results)} scenarios)")
            if plan.reason == "single-oversized":
                console.print(
                    "[yellow]Warning: payload exceeds the safe single-POST size "
                    "but has only one scenario — cannot chunk. POST may be "
                    "rejected by the endpoint.[/yellow]"
                )

        if dry_run:
            console.print("[yellow]dry-run: No HTTP calls made.[/yellow]")
            continue

        # --- Publish ---
        ok = _publish_one(
            console=console, url=url, fallback=fallback, headers=headers,
            full_payload=full_payload, plan=plan,
            build_chunk_payloads=build_chunk_payloads, inject_run_id=inject_run_id,
        )
        if not ok:
            any_failure = True

    if any_failure:
        raise click.exceptions.Exit(1)


def _publish_one(*, console, url, fallback, headers, full_payload, plan,
                 build_chunk_payloads, inject_run_id):
    """Publish a single run (single POST or chunked). Returns True on success."""
    if not plan.needs_chunking:
        status, body, err = _post_json(url, full_payload, headers, 60.0, fallback)
        return _report_result(console, status, body, err, chunk_label="run")

    chunks = build_chunk_payloads(full_payload, plan)
    run_id = None
    total = plan.total_chunks
    for i, chunk in enumerate(chunks, start=1):
        if i > 1:
            if not run_id:
                console.print("[red]Lost run_id after chunk 1 — aborting.[/red]")
                return False
            inject_run_id(chunk, run_id)

        n = len(chunk.get("results") or [])
        kb = payload_bytes_kb(chunk)
        console.print(f"[cyan]-> POST chunk {i}/{total} ({n} scenarios, {kb:.1f} KB)[/cyan]")
        status, body, err = _post_json(url, chunk, headers, 120.0, fallback)

        if status != 200:
            console.print(f"[red]Chunk {i}/{total} FAILED[/red]")
            _report_result(console, status, body, err, chunk_label=f"chunk {i}/{total}")
            if run_id:
                console.print(
                    f"[yellow]run_id so far: {run_id}[/yellow]\n"
                    f"[yellow]Run is in a NON-FINALIZED state; the scoreboard will "
                    f"NOT show it.[/yellow]\n"
                    f"[yellow]Do NOT naively retry — duplicate scenario_results "
                    f"would be inserted. Diagnose, then decide: abandon (orphan "
                    f"run) or DB-surgery to resume.[/yellow]"
                )
            return False

        if i == 1:
            run_id = (body or {}).get("run_id")
            if not run_id:
                console.print(
                    f"[red]Chunk 1 response missing run_id — cannot continue. "
                    f"Response: {body}[/red]"
                )
                return False
            console.print(f"[green]  run_id = {run_id}[/green]")

    console.print(
        f"[green]Published {plan.n_results} scenarios in {total} chunks. "
        f"run_id={run_id}[/green]"
    )
    return True


def payload_bytes_kb(payload):
    from sapien_score.publishing.chunking import payload_size_bytes
    return payload_size_bytes(payload) / 1024


def _report_result(console, status, body, err, chunk_label):
    """Print a friendly result line. Returns True on HTTP 200."""
    if status == 200:
        info = body or {}
        run_id = info.get("run_id", "?")
        scenarios = info.get("scenarios_processed", "?")
        console.print(
            f"[green]Published ({chunk_label}). run_id={run_id}, "
            f"scenarios={scenarios}[/green]"
        )
        return True

    msg = None
    if isinstance(body, dict):
        msg = body.get("error")
    if status is None:
        console.print(f"[red]Publishing failed ({chunk_label}): {err or 'no response'}.[/red]")
    elif status == 401:
        console.print(f"[red]Publishing failed ({chunk_label}): {msg or 'invalid API key'}.[/red]")
    elif status == 400:
        console.print(f"[red]Publishing failed ({chunk_label}): {msg or 'bad request'}.[/red]")
    elif status >= 500:
        console.print(f"[red]Publishing failed ({chunk_label}): server error (HTTP {status}).[/red]")
    else:
        console.print(f"[red]Publishing failed ({chunk_label}): {msg or f'HTTP {status}'}.[/red]")
    return False
