# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""voigt-kampff validate — orchestration layer.

Composes the three layers from ``sapien_score.validation`` into a
single command pipeline. Ports ``run_single``, ``apply_fixes``,
``pressure_calibration_check``, ``run_fix_mode``, the five
``render_*`` functions, and ``report_to_json`` from the original
``sapien_humanizer.py`` standalone (lines 705-1156). Also adds the
CLI surface: ``load_scenario``, ``find_scenarios``, ``interactive_mode``,
and the ``@click.command("validate")`` entry point registered in
``cli.py``.

Per the standing rule: every numeric threshold, string label, and
category list is a module-level named constant with a WHY docstring.
The package's existing constants (LEVEL_*, CATEGORY_*, HIGH_AI_PROBABILITY,
DEFAULT_AI_THRESHOLD, FIX_REPLACEMENTS) are imported, never redeclared.

Render functions accept an optional ``console`` parameter so tests
can inject a captured-buffer Console without depending on Rich's TTY
behavior, and so a future config-driven multi-console mode can plug
in without touching the renderers.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel

from sapien_score.validation.schema_check import (
    LEVEL_FAIL,
    LEVEL_PASS,
    LEVEL_WARN,
    SchemaResult,
    check_schema,
)
from sapien_score.validation.structure_check import (
    StructureReport,
    check_structure,
)
from sapien_score.validation.voice_check import (
    CATEGORY_BLADER_CHATBOT,
    CATEGORY_BLADER_FILLER,
    CATEGORY_SAPIEN_CRITICAL,
    CATEGORY_SAPIEN_FORMAL,
    DEFAULT_AI_THRESHOLD,
    FIX_REPLACEMENTS,
    HAS_LMSCAN,
    HIGH_AI_PROBABILITY,
    TURN_TYPE_ESCALATION,
    TURN_TYPE_HOLD_VARIANT,
    TURN_TYPE_OPENING,
    VoiceReport,
    check_voice,
    match_patterns,
    scan_text_lmscan,
    score_turn,
)


# ─── Orchestration tunables ─────────────────────────────────────────────────
# Single source of truth for orchestration thresholds. Each comment
# explains the choice so a future config-driven override path (CLI
# flag or ~/.voigt-kampff.toml) lands cleanly without re-implementing
# the call sites.

# Pressure-calibration max word-count drop. The fixer must not gut more
# than this fraction of a turn's words — beyond this the pressure
# payload (specific asks, qualifiers, escalation hooks) is at risk of
# being eroded into harmless prose.
WORD_COUNT_DROP_TOLERANCE: float = 0.30

# Turn-preview char count in voice panel rendering. Long enough to
# show the gist of the turn, short enough that one panel doesn't
# scroll off the terminal.
TURN_PREVIEW_LENGTH: int = 100

# Per-sentence preview char count when rendering the "hot sentences"
# breakdown under a flagged turn.
SENTENCE_PREVIEW_LENGTH: int = 80

# AI-probability cutoff for sentence-level "hot" highlight. Above this
# the sentence gets called out individually under its turn.
HOT_SENTENCE_THRESHOLD: float = 0.5

# Maximum number of hot sentences shown per flagged turn (sorted by
# descending AI probability). Caps panel size — the rest are still in
# the JSON output.
MAX_HOT_SENTENCES_SHOWN: int = 3


# ─── Fix-mode policy ────────────────────────────────────────────────────────
# Categories that have safe deterministic fixes in FIX_REPLACEMENTS.
# SAPIEN_CRITICAL is intentionally NOT in this set — those failures are
# meaning-bearing and require manual review, not regex substitution.
# Same for SAPIEN_TELL (rationalization tells), BLADER_VOCAB (vocab
# choices that vary by domain), BLADER_STYLE (sentence-structure tells
# that need rewriting, not deletion), and BLADER_CONTENT (factual
# claims that can't be auto-rewritten).

FIXABLE_CATEGORIES: tuple[str, ...] = (
    CATEGORY_SAPIEN_FORMAL,
    CATEGORY_BLADER_CHATBOT,
    CATEGORY_BLADER_FILLER,
)


# ─── Renderer cosmetics ─────────────────────────────────────────────────────
# Lifted from the standalone's level_icon() and ai_color() so the icon /
# color mapping is auditable from one place. Rich's color names are
# preserved verbatim so the rendered output is byte-identical.

LEVEL_ICONS: dict[str, str] = {
    LEVEL_PASS: "✅",
    LEVEL_WARN: "⚠️ ",
    LEVEL_FAIL: "❌",
}
LEVEL_ICON_FALLBACK: str = "❓"

# AI-probability → rich color thresholds. Reuses HIGH_AI_PROBABILITY (0.60)
# and DEFAULT_AI_THRESHOLD (0.40) from voice_check — single source of
# truth instead of redeclaring 0.6 / 0.4 here.
COLOR_HIGH_AI: str = "red"
COLOR_MEDIUM_AI: str = "yellow"
COLOR_LOW_AI: str = "green"
COLOR_UNAVAILABLE: str = "dim"

PANEL_BORDER_PASS: str = "green"
PANEL_BORDER_WARN: str = "yellow"
PANEL_BORDER_FAIL: str = "red"
PANEL_BORDER_SCHEMA: str = "blue"
PANEL_BORDER_FIX: str = "cyan"


# Default module-level console. Render functions accept an optional
# console kwarg so tests inject a captured-buffer Console and the
# Click command can pass through its own Console instance.
_default_console: Console = Console()


# ─── Composition type ──────────────────────────────────────────────────────

@dataclass
class FullReport:
    """Aggregated per-scenario report.

    Composes :class:`SchemaResult` rows (Layer 1) with a
    :class:`VoiceReport` (Layer 2). Layer 3 is domain-scoped, not
    per-scenario — see ``StructureReport`` returned separately by
    ``check_structure`` and rendered alongside the per-scenario
    reports in the corpus loop.
    """
    file_path: str = ""
    scenario_id: str = ""
    schema: list = field(default_factory=list)
    voice: VoiceReport = field(default_factory=VoiceReport)


# ─── Fix mode: deterministic humanizer ─────────────────────────────────────

def apply_fixes(text: str) -> str:
    """Apply deterministic regex replacements to remove AI tells.

    Pure regex — no LLM calls, no external deps. The replacement table
    lives in voice_check.FIX_REPLACEMENTS so the patterns and their
    fixes stay paired in one file. Cleanup passes after substitution
    collapse double spaces, eat orphan whitespace before punctuation,
    and strip leading commas/periods left when phrases were deleted.
    """
    fixed = text
    for pattern, replacement in FIX_REPLACEMENTS.items():
        fixed = re.sub(pattern, replacement, fixed)
    # Cleanup: collapse multi-spaces, remove space-before-punct, strip
    # any leading comma/period left by an empty-string replacement.
    fixed = re.sub(r"  +", " ", fixed).strip()
    fixed = re.sub(r"\s+([.,!?])", r"\1", fixed)
    fixed = re.sub(r"^\s*[.,]\s*", "", fixed)
    return fixed


def pressure_calibration_check(original: str, fixed: str) -> tuple[bool, str]:
    """Verify a fix didn't gut the scenario's pressure payload.

    Three guards, in order — each returns False as soon as it trips so
    the message names the actual failure mode:

      1. Word-count drop above WORD_COUNT_DROP_TOLERANCE — fixer ate
         too much; the turn no longer carries enough content to apply
         meaningful pressure.
      2. Question marks lost — questions are usually the pressure
         payload ("can you tell me…", "what if I…"); losing any is a
         signal the fixer rewrote the ask out of the turn.
      3. Fix introduced a new SAPIEN_CRITICAL pattern — by accident,
         a substitution can compose into one of the auto-FAIL patterns;
         if so, reject the fix.

    Returns ``(ok, message)``. The message is rendered to the user in
    fix-mode logs.
    """
    orig_words = len(original.split())
    fixed_words = len(fixed.split())

    if orig_words > 0 and (orig_words - fixed_words) / orig_words > WORD_COUNT_DROP_TOLERANCE:
        ratio = (orig_words - fixed_words) / orig_words
        return False, (
            f"Word count dropped {orig_words}→{fixed_words} ({ratio:.0%})"
        )

    orig_questions = len(re.findall(r"\?", original))
    fixed_questions = len(re.findall(r"\?", fixed))
    if orig_questions > 0 and fixed_questions < orig_questions:
        return False, f"Lost questions: {orig_questions}→{fixed_questions}"

    new_critical = [
        p for p in match_patterns(fixed)
        if p.category == CATEGORY_SAPIEN_CRITICAL
    ]
    if new_critical:
        return False, "Fix introduced SAPIEN_CRITICAL patterns"

    return True, "Calibration OK"


def run_fix_mode(scenario: dict, path: str, threshold: float) -> list[str]:
    """Run deterministic fixes on flagged turns.

    Writes back to JSON only if every applied fix passes pressure
    calibration AND lmscan reports an improved score (or lmscan is
    unavailable). Returns a list of human-readable log lines for the
    renderer.
    """
    log: list[str] = []
    modified = False
    escalations = scenario.get("escalations", [])

    for i, esc in enumerate(escalations):
        prompt = esc.get("prompt", "")
        patterns = match_patterns(prompt)
        fixable = [p for p in patterns if p.category in FIXABLE_CATEGORIES]

        scan = scan_text_lmscan(prompt)
        needs_fix = bool(fixable) or (scan["ai_probability"] > threshold)
        if not needs_fix:
            continue

        old_score = scan["ai_probability"]
        fixed = apply_fixes(prompt)
        if fixed == prompt:
            continue

        ok, msg = pressure_calibration_check(prompt, fixed)
        if not ok:
            log.append(
                f"T{i}: humanizer reduced pressure — {msg} — manual review needed"
            )
            continue

        new_scan = scan_text_lmscan(fixed)
        new_score = new_scan["ai_probability"]

        # When lmscan is available, only accept the fix if it actually
        # improved the score — otherwise we're churning text for no
        # measurable gain.
        if HAS_LMSCAN and new_score >= old_score:
            log.append(
                f"T{i}: fix didn't improve score "
                f"({old_score:.2f}→{new_score:.2f}) — skipped"
            )
            continue

        esc["prompt"] = fixed
        modified = True
        old_s = f"{old_score:.2f}" if old_score >= 0 else "N/A"
        new_s = f"{new_score:.2f}" if new_score >= 0 else "N/A"
        log.append(f"T{i}: fixed {old_s} → {new_s}")

        # Hold-variant fixes are gated on having a fixable pattern in
        # the variant itself — we never blindly apply fixes to clean
        # variant text just because the parent prompt had issues.
        for vi, variant in enumerate(esc.get("hold_variants", [])):
            v_patterns = match_patterns(variant)
            v_fixable = [p for p in v_patterns if p.category in FIXABLE_CATEGORIES]
            if not v_fixable:
                continue
            v_fixed = apply_fixes(variant)
            if v_fixed == variant:
                continue
            v_ok, v_msg = pressure_calibration_check(variant, v_fixed)
            if not v_ok:
                log.append(
                    f"T{i}/HV{vi}: reduced pressure — {v_msg} — skipped"
                )
                continue
            esc["hold_variants"][vi] = v_fixed
            modified = True
            log.append(f"T{i}/HV{vi}: fixed")

    if modified:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(scenario, f, indent=2, ensure_ascii=False)
        log.append(f"Wrote updated scenario to {path}")

    return log


# ─── Rich rendering helpers ─────────────────────────────────────────────────

def level_icon(level: str) -> str:
    """Return the emoji icon for a LEVEL_* string, with fallback."""
    return LEVEL_ICONS.get(level, LEVEL_ICON_FALLBACK)


def ai_color(prob: float) -> str:
    """Map an AI-probability score to a rich color name.

    Negative values mean "not scored" — rendered dim so they don't
    register as a green-pass. The two thresholds are imported from
    voice_check (HIGH_AI_PROBABILITY, DEFAULT_AI_THRESHOLD) instead of
    being redeclared here.
    """
    if prob < 0:
        return COLOR_UNAVAILABLE
    if prob >= HIGH_AI_PROBABILITY:
        return COLOR_HIGH_AI
    if prob >= DEFAULT_AI_THRESHOLD:
        return COLOR_MEDIUM_AI
    return COLOR_LOW_AI


def _border_for_level(level: str) -> str:
    """Map a LEVEL_* string to its panel border color."""
    if level == LEVEL_PASS:
        return PANEL_BORDER_PASS
    if level == LEVEL_WARN:
        return PANEL_BORDER_WARN
    return PANEL_BORDER_FAIL


def render_schema_panel(
    results: list[SchemaResult],
    console: Optional[Console] = None,
) -> None:
    """Render the Layer-1 schema check results panel."""
    target = console or _default_console
    lines = [
        f"{level_icon(r.level)} {r.level}  {r.check_name}: {r.message}"
        for r in results
    ]
    target.print(Panel("\n".join(lines), title="Schema", border_style=PANEL_BORDER_SCHEMA))


def render_voice_panel(
    report: VoiceReport,
    verbose: bool = False,
    threshold: float = DEFAULT_AI_THRESHOLD,
    console: Optional[Console] = None,
) -> None:
    """Render the Layer-2 voice quality results panel.

    Clean turns get a one-liner unless ``verbose=True``. Flagged turns
    expand into a full breakdown including pattern descriptions, the
    matched text, and (when verbose or above-threshold) up to
    MAX_HOT_SENTENCES_SHOWN per-sentence AI scores.
    """
    target = console or _default_console
    lines: list[str] = []

    if report.overall_ai_probability >= 0:
        prob_str = f"{report.overall_ai_probability:.0%}"
    else:
        prob_str = "N/A (no lmscan)"
    lines.append(
        f"Overall AI probability: "
        f"[{ai_color(report.overall_ai_probability)}]{prob_str}[/]"
    )
    lines.append(
        f"Critical patterns: {report.critical_count}  |  "
        f"Total patterns: {report.pattern_count}"
    )
    lines.append("")

    for ts in report.turn_scores:
        if ts.turn_type == TURN_TYPE_OPENING:
            label = "Opening"
        elif ts.turn_type == TURN_TYPE_HOLD_VARIANT:
            label = f"  └─ HV{ts.variant_index} (T{ts.parent_turn})"
        else:
            label = f"T{ts.turn_index}"

        prob = f"{ts.ai_probability:.0%}" if ts.ai_probability >= 0 else "N/A"
        color = ai_color(ts.ai_probability)

        has_issues = (
            ts.pattern_matches
            or ts.uniformity_warning
            or ts.ai_probability > threshold
        )

        if not has_issues and not verbose:
            lines.append(f"{level_icon(LEVEL_PASS)} {label}: [{color}]{prob}[/]")
            continue

        # Flagged turn: pick icon based on worst pattern category
        has_critical = any(
            p.category == CATEGORY_SAPIEN_CRITICAL for p in ts.pattern_matches
        )
        if has_critical:
            icon = level_icon(LEVEL_FAIL)
        elif has_issues:
            icon = level_icon(LEVEL_WARN)
        else:
            icon = level_icon(LEVEL_PASS)

        lines.append(f"{icon} {label}: [{color}]{prob}[/]  [{ts.confidence}]")

        preview = ts.text[:TURN_PREVIEW_LENGTH].replace("\n", " ")
        if len(ts.text) > TURN_PREVIEW_LENGTH:
            preview += "..."
        lines.append(f"    [dim]{preview}[/]")

        for flag in ts.lmscan_flags:
            lines.append(f"    [yellow]⚠ {flag}[/]")

        for pm in ts.pattern_matches:
            # Critical patterns render red, everything else yellow.
            # Exact equality with CATEGORY_SAPIEN_CRITICAL is more
            # robust than a substring scan for "CRITICAL".
            c = "red" if pm.category == CATEGORY_SAPIEN_CRITICAL else "yellow"
            lines.append(f"    [{c}]✗ [{pm.category}] {pm.pattern_name}[/]")
            lines.append(f"      Matched: \"{pm.matched_text}\"")
            lines.append(f"      Fix: {pm.description}")

        if ts.uniformity_warning:
            lines.append(f"    [yellow]⚠ {ts.uniformity_warning}[/]")

        if (verbose or ts.ai_probability > threshold) and ts.sentence_scores:
            hot = [s for s in ts.sentence_scores if s.ai_probability >= HOT_SENTENCE_THRESHOLD]
            if hot:
                lines.append(f"    [dim]── Hot sentences (≥{int(HOT_SENTENCE_THRESHOLD * 100)}% AI):[/]")
                for s in sorted(hot, key=lambda x: -x.ai_probability)[:MAX_HOT_SENTENCES_SHOWN]:
                    st = s.text[:SENTENCE_PREVIEW_LENGTH]
                    lines.append(
                        f"      [{ai_color(s.ai_probability)}]{s.ai_probability:.0%}[/] [dim]{st}[/]"
                    )

    if report.uniformity_warnings:
        lines.append("")
        for w in report.uniformity_warnings:
            lines.append(f"⚠️  [yellow]{w}[/]")

    border = _border_for_level(report.pass_fail)
    target.print(Panel("\n".join(lines), title="Voice Quality", border_style=border))


def render_structure_panel(
    report: StructureReport,
    console: Optional[Console] = None,
) -> None:
    """Render the Layer-3 structural variety results panel."""
    target = console or _default_console
    lines = [
        f"{level_icon(r.level)} {r.level}  {r.check_name}: {r.message}"
        for r in report.results
    ]
    title = f"Structure (domain: {report.domain}, {report.scenario_count} scenarios)"
    border = _border_for_level(report.pass_fail)
    target.print(Panel("\n".join(lines), title=title, border_style=border))


def render_batch_line(
    report: FullReport,
    console: Optional[Console] = None,
) -> None:
    """Render a single one-line batch summary for a scenario."""
    target = console or _default_console
    sid = report.scenario_id
    v = report.voice
    if v.overall_ai_probability >= 0:
        ai_str = f"AI: {v.overall_ai_probability:.0%}"
    else:
        ai_str = "AI: N/A"
    color = ai_color(v.overall_ai_probability)

    worst = ""
    worst_turns = [
        t for t in v.turn_scores
        if t.ai_probability > DEFAULT_AI_THRESHOLD
        and t.turn_type == TURN_TYPE_ESCALATION
    ]
    if worst_turns:
        wt = max(worst_turns, key=lambda t: t.ai_probability)
        worst = f" (T{wt.turn_index}: {wt.ai_probability:.2f})"

    schema_fail = any(r.level == LEVEL_FAIL for r in report.schema)
    overall = LEVEL_FAIL if schema_fail or v.pass_fail == LEVEL_FAIL else v.pass_fail
    target.print(f"  {sid:<50} [{color}]{ai_str}[/]  {overall}{worst}")


def render_summary(
    reports: list[FullReport],
    domain: Optional[str] = None,
    console: Optional[Console] = None,
) -> None:
    """Render a domain or corpus pass/warn/fail summary line."""
    target = console or _default_console
    passes = sum(
        1 for r in reports
        if r.voice.pass_fail == LEVEL_PASS
        and not any(s.level == LEVEL_FAIL for s in r.schema)
    )
    warns = sum(
        1 for r in reports
        if r.voice.pass_fail == LEVEL_WARN
        and not any(s.level == LEVEL_FAIL for s in r.schema)
    )
    fails = len(reports) - passes - warns
    label = f"Domain: {domain}" if domain else "Corpus"
    target.print(
        f"\n{label} ({len(reports)} scenarios) | "
        f"[green]{passes} PASS[/] | "
        f"[yellow]{warns} WARN[/] | "
        f"[red]{fails} FAIL[/]"
    )


# ─── JSON output ────────────────────────────────────────────────────────────

def report_to_json(
    reports: list[FullReport],
    mode: str,
    threshold: float,
    structures: Optional[list[StructureReport]] = None,
) -> dict:
    """Serialize a list of FullReports into the standalone's JSON shape.

    Behavior pinned by equivalence tests against sapien_humanizer.py —
    field names, types, and nested structure must remain identical.
    Per-turn entries are emitted only for turns that have pattern_matches
    OR an above-threshold AI probability; clean turns are omitted to keep
    the report concise.
    """
    passes = sum(
        1 for r in reports
        if r.voice.pass_fail == LEVEL_PASS
        and not any(s.level == LEVEL_FAIL for s in r.schema)
    )
    warns = sum(1 for r in reports if r.voice.pass_fail == LEVEL_WARN)
    fails = len(reports) - passes - warns

    out: dict = {
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "threshold": threshold,
        "lmscan_available": HAS_LMSCAN,
        "scenarios_checked": len(reports),
        "pass": passes,
        "warn": warns,
        "fail": fails,
        "results": [],
    }

    for r in reports:
        entry: dict = {
            "file": r.file_path,
            "scenario_id": r.scenario_id,
            "schema": {
                "level": LEVEL_FAIL if any(s.level == LEVEL_FAIL for s in r.schema) else LEVEL_PASS,
                "checks": [
                    {"level": s.level, "check": s.check_name, "message": s.message}
                    for s in r.schema
                ],
            },
            "voice": {
                "level": r.voice.pass_fail,
                "overall_ai_probability": (
                    round(r.voice.overall_ai_probability, 4)
                    if r.voice.overall_ai_probability >= 0 else None
                ),
                "critical_count": r.voice.critical_count,
                "pattern_count": r.voice.pattern_count,
                "turns": [],
            },
        }
        for ts in r.voice.turn_scores:
            if ts.pattern_matches or ts.ai_probability > threshold:
                entry["voice"]["turns"].append({
                    "turn": ts.turn_index,
                    "type": ts.turn_type,
                    "ai_probability": (
                        round(ts.ai_probability, 4)
                        if ts.ai_probability >= 0 else None
                    ),
                    "patterns": [
                        {"name": p.pattern_name, "matched": p.matched_text,
                         "category": p.category}
                        for p in ts.pattern_matches
                    ],
                })
        out["results"].append(entry)

    if structures:
        out["domain_structure"] = {}
        for s in structures:
            out["domain_structure"][s.domain] = {
                "level": s.pass_fail,
                "checks": [
                    {"level": r.level, "check": r.check_name, "message": r.message}
                    for r in s.results
                ],
            }

    return out


# ─── Single-scenario orchestration ──────────────────────────────────────────

def run_single(
    scenario: dict,
    path: str,
    threshold: float,
    verbose: bool = False,
    fix: bool = False,
    strict: bool = False,
    console: Optional[Console] = None,
) -> FullReport:
    """Run all checks on a single scenario and render to ``console``.

    Order matters:
      1. Layer 1 (schema) renders first so reviewers see structural
         issues before voice noise.
      2. Fix mode runs BEFORE Layer 2 so the voice scores reflect the
         fixed text, not the pre-fix text.
      3. Layer 2 (voice) renders last; ``strict`` mode escalates any
         above-threshold turn to FAIL after the fact.

    Returns the populated :class:`FullReport`. Layer 3 isn't run here —
    structural checks are domain-scoped and live in the ``validate()``
    Click command's corpus loop.
    """
    target = console or _default_console

    report = FullReport(
        file_path=str(path),
        scenario_id=scenario.get("id", "unknown"),
    )

    report.schema = check_schema(scenario)
    render_schema_panel(report.schema, console=target)

    if fix:
        fix_log = run_fix_mode(scenario, path, threshold)
        if fix_log:
            target.print(Panel(
                "\n".join(fix_log),
                title="Fix Mode",
                border_style=PANEL_BORDER_FIX,
            ))

    report.voice = check_voice(scenario, threshold)
    if strict:
        for ts in report.voice.turn_scores:
            if ts.ai_probability > threshold:
                report.voice.pass_fail = LEVEL_FAIL
                break
    render_voice_panel(
        report.voice,
        verbose=verbose,
        threshold=threshold,
        console=target,
    )

    return report


# ─── CLI exit codes ─────────────────────────────────────────────────────────
# POSIX-style: 0 success, 1 generic error. Lifted to constants so command
# code never spells `sys.exit(1)` inline — callers (and tests) reference
# the named code instead.

EXIT_OK: int = 0
EXIT_ERROR: int = 1


# ─── CLI flag defaults ──────────────────────────────────────────────────────
# Defaults Click hands to options when the user omits the flag. Lifted
# so future config-file overrides have a single place to plug in.

DEFAULT_SCENARIOS_DIR: str = "."

# Reserved turn type for free-form text scored in interactive mode.
# Distinct from TURN_TYPE_OPENING/ESCALATION/HOLD_VARIANT so any code
# that filters on those three doesn't accidentally pick up pasted text.
TURN_TYPE_PASTED: str = "pasted"


# ─── Scenario loading ──────────────────────────────────────────────────────

def load_scenario(path: str, console: Optional[Console] = None) -> dict:
    """Load and parse a scenario JSON file.

    On any I/O or parse error, prints a red error line and exits with
    EXIT_ERROR. Matches the standalone's behavior — a missing file or
    malformed JSON is unrecoverable from the validate command's view.
    """
    target = console or _default_console
    p = Path(path)
    if not p.exists():
        target.print(f"[red]File not found: {path}[/]")
        sys.exit(EXIT_ERROR)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        target.print(f"[red]Invalid JSON in {path}: {e}[/]")
        sys.exit(EXIT_ERROR)


def find_scenarios(
    base_dir: str,
    domain: Optional[str] = None,
    console: Optional[Console] = None,
) -> list[tuple[str, Path]]:
    """Find scenario JSON files under ``base_dir``.

    Returns a list of ``(domain_name, file_path)`` tuples. When
    ``domain`` is given, restricts to that single subdirectory; when
    omitted, walks every direct subdirectory (each one treated as a
    domain). On a missing base_dir or domain, prints a red error and
    exits with EXIT_ERROR.
    """
    target = console or _default_console
    base = Path(base_dir)
    if not base.exists():
        target.print(f"[red]Directory not found: {base_dir}[/]")
        sys.exit(EXIT_ERROR)

    results: list[tuple[str, Path]] = []
    if domain:
        domain_dir = base / domain
        if not domain_dir.exists():
            target.print(f"[red]Domain directory not found: {domain_dir}[/]")
            sys.exit(EXIT_ERROR)
        for f in sorted(domain_dir.glob("*.json")):
            results.append((domain, f))
    else:
        for domain_dir in sorted(base.iterdir()):
            if domain_dir.is_dir():
                for f in sorted(domain_dir.glob("*.json")):
                    results.append((domain_dir.name, f))
    return results


# ─── Interactive mode ──────────────────────────────────────────────────────

def interactive_mode(
    path: str,
    threshold: float,
    verbose: bool,
    console: Optional[Console] = None,
    input_fn=input,
) -> None:
    """Score → review → paste revised text → verify → save loop.

    First runs a full single-scenario audit, then enters a paste-and-
    rescore REPL. Commands inside the loop:

      ``q``       — quit
      ``report``  — re-run the full single-scenario audit
      <text>      — first line of pasted turn; subsequent lines until
                    a blank line are concatenated and scored as a
                    single turn

    ``input_fn`` is parameterized so tests can inject a queued response
    sequence without touching real stdin.
    """
    target = console or _default_console
    scenario = load_scenario(path, console=target)
    run_single(scenario, path, threshold, verbose=verbose, console=target)

    target.print("\n[bold]Interactive re-score mode[/]")
    target.print("Paste revised turn text, then press Enter twice to score.")
    target.print("Type 'q' to quit, 'report' to re-run full audit.\n")

    while True:
        try:
            cmd = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            target.print("\nDone.")
            break

        if cmd.lower() == "q":
            break
        if cmd.lower() == "report":
            scenario = load_scenario(path, console=target)
            run_single(scenario, path, threshold, verbose=verbose, console=target)
            continue
        if cmd == "":
            continue

        # Multi-line capture: accumulate until blank line / EOF.
        lines = [cmd]
        while True:
            try:
                line = input_fn("")
                if line == "":
                    break
                lines.append(line)
            except EOFError:
                break

        text = "\n".join(lines)
        if not text.strip():
            continue

        ts = score_turn(text, turn_index=0, turn_type=TURN_TYPE_PASTED)
        prob = f"{ts.ai_probability:.0%}" if ts.ai_probability >= 0 else "N/A"
        color = ai_color(ts.ai_probability)

        target.print(f"\n  AI Probability: [{color}]{prob}[/]  [{ts.confidence}]")
        for flag in ts.lmscan_flags:
            target.print(f"  [yellow]⚠ {flag}[/]")
        for pm in ts.pattern_matches:
            c = "red" if pm.category == CATEGORY_SAPIEN_CRITICAL else "yellow"
            target.print(f"  [{c}]✗ {pm.pattern_name}: \"{pm.matched_text}\"[/]")
        if ts.uniformity_warning:
            target.print(f"  [yellow]⚠ {ts.uniformity_warning}[/]")
        if not ts.pattern_matches and ts.ai_probability < threshold:
            target.print("  [green]✓ Clean — this turn reads human.[/]")
        target.print()


# ─── Click command ─────────────────────────────────────────────────────────

@click.command("validate")
@click.option("--scenario", type=click.Path(), default=None,
              help="Path to a single scenario JSON.")
@click.option("--domain", type=str, default=None,
              help="Validate every scenario in <scenarios-dir>/<domain>/.")
@click.option("--all", "validate_all", is_flag=True,
              help="Validate the entire corpus under <scenarios-dir>.")
@click.option("--scenarios-dir", type=click.Path(), default=DEFAULT_SCENARIOS_DIR,
              show_default=True,
              help="Base directory holding domain subdirectories.")
@click.option("--fix", is_flag=True,
              help="Run the deterministic humanizer on flagged turns.")
@click.option("--strict", is_flag=True,
              help="Treat any above-threshold lmscan score as a FAIL.")
@click.option("--threshold", type=float, default=DEFAULT_AI_THRESHOLD,
              show_default=True,
              help="AI probability threshold for WARN/FAIL.")
@click.option("--verbose", is_flag=True,
              help="Show per-sentence AI scores on every turn.")
@click.option("--output", type=click.Path(), default=None,
              help="Write the validation report to this JSON file.")
@click.option("--batch", is_flag=True,
              help="One-line-per-scenario output (skips full panels).")
@click.option("--interactive", is_flag=True,
              help="Score → edit → re-score loop (single scenario only).")
def validate(
    scenario: Optional[str],
    domain: Optional[str],
    validate_all: bool,
    scenarios_dir: str,
    fix: bool,
    strict: bool,
    threshold: float,
    verbose: bool,
    output: Optional[str],
    batch: bool,
    interactive: bool,
) -> None:
    """Three-layer scenario quality gate (schema + voice + structure)."""
    console = _default_console

    if not HAS_LMSCAN:
        console.print(
            "[dim]ℹ Install lmscan for AI detection scoring: "
            "pip install lmscan[/]"
        )

    # ── Single scenario ──
    if scenario:
        if interactive:
            interactive_mode(scenario, threshold, verbose, console=console)
            return

        data = load_scenario(scenario, console=console)
        report = run_single(
            data, scenario, threshold,
            verbose=verbose, fix=fix, strict=strict,
            console=console,
        )

        schema_fail = any(r.level == LEVEL_FAIL for r in report.schema)
        if schema_fail or report.voice.pass_fail == LEVEL_FAIL:
            overall = LEVEL_FAIL
        else:
            overall = report.voice.pass_fail
        console.print(f"\n[bold]Result: {overall}[/]")

        if output:
            out = report_to_json([report], "single", threshold)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            console.print(f"[dim]Report saved to {output}[/]")
        return

    # ── Domain or corpus ──
    if domain or validate_all:
        scenario_files = find_scenarios(
            scenarios_dir,
            domain if domain else None,
            console=console,
        )
        if not scenario_files:
            console.print("[red]No scenario files found.[/]")
            sys.exit(EXIT_ERROR)

        # Group by domain so Layer 3 can run on each domain bucket.
        by_domain: dict[str, list[tuple[Path, dict]]] = {}
        for d, p in scenario_files:
            by_domain.setdefault(d, []).append(
                (p, load_scenario(str(p), console=console))
            )

        all_reports: list[FullReport] = []
        all_structures: list[StructureReport] = []

        for d, items in sorted(by_domain.items()):
            if not batch:
                console.print(
                    f"\n[bold]═══ Domain: {d} ({len(items)} scenarios) ═══[/]"
                )

            domain_reports: list[FullReport] = []
            for p, sc in items:
                if batch:
                    rep = FullReport(
                        file_path=str(p),
                        scenario_id=sc.get("id", "unknown"),
                    )
                    rep.schema = check_schema(sc)
                    rep.voice = check_voice(sc, threshold)
                    render_batch_line(rep, console=console)
                else:
                    console.print(
                        f"\n[bold]── {sc.get('id', p.name)} ──[/]"
                    )
                    rep = run_single(
                        sc, str(p), threshold,
                        verbose=verbose, fix=fix, strict=strict,
                        console=console,
                    )
                domain_reports.append(rep)
                all_reports.append(rep)

            structure = check_structure([s for _, s in items], d)
            all_structures.append(structure)
            if not batch:
                render_structure_panel(structure, console=console)

            render_summary(domain_reports, d, console=console)

        if output:
            mode = "domain" if domain else "corpus"
            out = report_to_json(
                all_reports, mode, threshold, all_structures,
            )
            with open(output, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            console.print(f"\n[dim]Report saved to {output}[/]")
        return

    # No mode selected — show help instead of silently exiting.
    ctx = click.get_current_context()
    click.echo(ctx.get_help())
