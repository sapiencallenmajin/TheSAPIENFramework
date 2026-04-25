# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""HTTP client for publishing scan results to the SAPIEN scoreboard.

Wraps httpx for a single POST to the ingestion endpoint.  All errors
are caught and surfaced as console warnings — publish never causes
non-zero exit.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)

DEFAULT_INGEST_URL = "https://sapienframework.org/api/ingest-results/"
FALLBACK_INGEST_URL = "https://the-sapien-framework.vercel.app/api/ingest-results/"

# Judge family inference from LiteLLM model prefixes.
# Covers the provider prefixes used in the sapien-score ecosystem.
_JUDGE_FAMILY_MAP = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "vertex_ai": "Google",
    "google": "Google",
    "gemini": "Google",
    "mistral": "Mistral",
    "cohere": "Cohere",
}

# Bedrock model IDs embed the provider after the region prefix:
# bedrock/us.anthropic.claude-... -> Anthropic
# bedrock/us.deepseek.v3.2 -> DeepSeek
_BEDROCK_FAMILY_MAP = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "meta": "Meta",
    "mistral": "Mistral",
    "amazon": "Amazon",
    "cohere": "Cohere",
    "qwen": "Qwen",
}


def infer_judge_family(judge_model: str) -> Optional[str]:
    """Infer judge_family from the LiteLLM model string.

    Returns the family name (e.g. "OpenAI") or None if unknown.
    """
    if not judge_model:
        return None

    prefix = judge_model.split("/")[0].lower() if "/" in judge_model else ""

    # Direct prefix match (openai/gpt-4o -> OpenAI)
    if prefix in _JUDGE_FAMILY_MAP:
        return _JUDGE_FAMILY_MAP[prefix]

    # Bedrock: parse provider from model ID after region prefix
    if prefix == "bedrock":
        # bedrock/us.anthropic.claude-... -> anthropic
        model_part = judge_model.split("/", 1)[1] if "/" in judge_model else ""
        segments = model_part.split(".")
        for seg in segments:
            seg_lower = seg.lower()
            if seg_lower in _BEDROCK_FAMILY_MAP:
                return _BEDROCK_FAMILY_MAP[seg_lower]

    return None


def resolve_judge_family(
    judge_model: Optional[str],
    console: "Console",
) -> Optional[str]:
    """Resolve judge_family from env var or inference.

    Priority: SAPIEN_JUDGE_FAMILY env var > inference from model string.
    Prints a warning when inference is used.
    Returns None if no judge is configured.
    """
    if not judge_model:
        return None

    env_family = os.environ.get("SAPIEN_JUDGE_FAMILY")
    if env_family:
        return env_family

    inferred = infer_judge_family(judge_model)
    if inferred:
        console.print(
            f"[dim]judge_family inferred as '{inferred}' from '{judge_model}'. "
            f"Set SAPIEN_JUDGE_FAMILY to suppress.[/dim]"
        )
        return inferred

    console.print(
        f"[yellow]Could not infer judge_family from '{judge_model}'. "
        f"Set SAPIEN_JUDGE_FAMILY environment variable.[/yellow]"
    )
    return None


_TRANSCRIPT_FIELDS = ("user_message", "assistant_response")


def _strip_transcripts(output_data: dict) -> dict:
    """Return a deep-ish copy of *output_data* with per-turn transcripts removed.

    Preserves scores, dimensions, health, verdicts, timings, and all
    top-level metadata so the scoreboard retains everything it needs to
    plot and rank — but drops the verbatim ``user_message`` and
    ``assistant_response`` text. Persona / memory strings and any
    credentials a misconfigured provider echoed back stay local.
    """
    stripped = dict(output_data)
    results = stripped.get("results")
    if isinstance(results, list):
        new_results = []
        for entry in results:
            if not isinstance(entry, dict):
                new_results.append(entry)
                continue
            new_entry = dict(entry)
            turns = new_entry.get("turns")
            if isinstance(turns, list):
                new_turns = []
                for turn in turns:
                    if isinstance(turn, dict):
                        new_turn = {
                            k: v for k, v in turn.items()
                            if k not in _TRANSCRIPT_FIELDS
                        }
                        new_turns.append(new_turn)
                    else:
                        new_turns.append(turn)
                new_entry["turns"] = new_turns
            new_results.append(new_entry)
        stripped["results"] = new_results
    return stripped


def publish_results(
    *,
    console: "Console",
    output_data: dict,
    judge_model: Optional[str],
    judge_family: Optional[str],
    run_label: str,
    is_primary: bool,
    publish_url: Optional[str],
    publisher: Optional[str] = None,
    publish_transcripts: bool = False,
) -> None:
    """POST scan results to the SAPIEN scoreboard.

    Never raises — all errors are printed as warnings.

    By default, per-turn ``user_message`` and ``assistant_response`` text
    is stripped from each result entry before transmit — scores and
    metadata go to the scoreboard but raw transcripts (which may embed
    persona / memory context or provider-echoed credentials) stay local.
    Pass ``publish_transcripts=True`` to opt in to sending full text.
    """
    import httpx

    from sapien_score.__version__ import __version__

    api_key = os.environ.get("SAPIEN_INGEST_API_KEY", "")
    if not api_key:
        console.print(
            "[yellow]--publish requires SAPIEN_INGEST_API_KEY. "
            "Skipping publish.[/yellow]"
        )
        return

    url = publish_url or os.environ.get("SAPIEN_INGEST_URL", DEFAULT_INGEST_URL)

    # Build payload: existing scan output + metadata fields.
    # output_data already carries run_id, scan_started_at, scan_finished_at,
    # content_hash, _checksum, n_requested, n_completed, n_failed — schema
    # v3 is the first version where all of those are required server-side.
    if publish_transcripts:
        payload = dict(output_data)
    else:
        payload = _strip_transcripts(output_data)
        payload["transcripts_stripped"] = True
    payload["judge_model"] = judge_model
    payload["judge_family"] = judge_family
    payload["run_label"] = run_label
    payload["is_primary"] = is_primary
    payload["cli_version"] = __version__
    payload["schema_version"] = 3
    # Surface scoring metadata at the top level so the ingest endpoint
    # can record scoring_mode / council_size on the runs row without
    # having to introspect per-scenario council_scoring objects.
    has_council = any(r.get("council_scoring") for r in output_data.get("results", []))
    if has_council:
        sample = next(r["council_scoring"] for r in output_data["results"] if r.get("council_scoring"))
        payload["scoring_mode"] = "council"
        payload["council_size"] = len(sample.get("individual_scores") or []) or None
    else:
        payload["scoring_mode"] = "single"
    if publisher is not None:
        payload["publisher"] = publisher

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Try primary URL, fall back to Vercel if DNS fails
    urls_to_try = [url]
    if url == DEFAULT_INGEST_URL:
        urls_to_try.append(FALLBACK_INGEST_URL)

    response = None
    for attempt_url in urls_to_try:
        try:
            response = httpx.post(attempt_url, json=payload, headers=headers, timeout=30.0)
            break
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout) as exc:
            if attempt_url != urls_to_try[-1]:
                logger.debug("Primary URL failed (%s), trying fallback", exc)
                continue
            console.print(
                f"[yellow]Publishing failed: {exc}. "
                f"Scoreboard may be unavailable.[/yellow]"
            )
            return
        except Exception as exc:
            console.print(f"[yellow]Publishing failed: {exc}[/yellow]")
            return

    if response is None:
        return

    if response.status_code == 200:
        try:
            data = response.json()
            run_id = data.get("run_id", "unknown")
            scenarios = data.get("scenarios_processed", "?")
            domains = data.get("domains_processed", "?")
            console.print(
                f"[green]Published to scoreboard. "
                f"Run ID: {run_id}, Scenarios: {scenarios}, "
                f"Domains: {domains}[/green]"
            )
        except Exception:
            console.print(
                "[green]Published to scoreboard.[/green]"
            )
        return

    # Error responses
    error_msg = None
    try:
        data = response.json()
        error_msg = data.get("error")
    except Exception:
        pass

    if response.status_code == 401:
        console.print(
            f"[yellow]Publishing failed: "
            f"{error_msg or 'invalid API key'}.[/yellow]"
        )
    elif response.status_code == 400:
        console.print(
            f"[yellow]Publishing failed: "
            f"{error_msg or 'bad request'}.[/yellow]"
        )
    elif response.status_code >= 500:
        console.print(
            "[yellow]Publishing failed: server error. "
            "Try again later.[/yellow]"
        )
    else:
        console.print(
            f"[yellow]Publishing failed: "
            f"{error_msg or f'HTTP {response.status_code}'}.[/yellow]"
        )
