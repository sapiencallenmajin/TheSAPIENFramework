# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""
HTML report generator for SAPIEN Score scan results.

Produces a self-contained, single-file HTML report with all CSS inline.
No JavaScript required. Designed to be professional enough to share with
stakeholders and managers.

Usage::

    from sapien_score.reporting.html_report import generate_html_report
    generate_html_report(results, "claude-sonnet-4-20250514", "report.html")
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sapien_score.__version__ import __version__
from sapien_score.scoring.health import (
    HEALTH_RATING_BANDS,
    DIMENSION_LABELS,
    DIMENSION_WEIGHTS,
    calculate_health_score,
)

if TYPE_CHECKING:
    from sapien_score.engine.driver import ScenarioResult

__all__ = ["generate_html_report"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rating_for_score(score: int) -> tuple[str, str, str]:
    """Return (label, fg_hex, bg_hex) for a health score."""
    for min_score, label, fg, bg in HEALTH_RATING_BANDS:
        if score >= min_score:
            return label, fg, bg
    return HEALTH_RATING_BANDS[-1][1], HEALTH_RATING_BANDS[-1][2], HEALTH_RATING_BANDS[-1][3]


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text))


def _dimension_display(key: str) -> str:
    """Return public-facing label for a dimension key."""
    return DIMENSION_LABELS.get(key, key.replace("_", " ").title())


def _compute_scenario_health(result: "ScenarioResult") -> dict:
    """Compute health score dict from a ScenarioResult's turns."""
    if not result.turns:
        return calculate_health_score({})

    # Average dimension scores across all turns
    dim_totals: dict[str, float] = {}
    dim_counts: dict[str, int] = {}
    for turn in result.turns:
        for dim_score in turn.scores.dimensions:
            dim_totals[dim_score.dimension] = dim_totals.get(dim_score.dimension, 0.0) + dim_score.drift
            dim_counts[dim_score.dimension] = dim_counts.get(dim_score.dimension, 0) + 1

    dim_avgs = {
        dim: dim_totals[dim] / dim_counts[dim]
        for dim in dim_totals
    }

    # layer1.py and health.py already share the canonical dimension keys
    # (specificity_control, risk_disclosure, epistemic_integrity,
    # emotional_reasoning), so no remapping is needed. An earlier key_map
    # here shadowed the real keys and silently zeroed every per-scenario
    # health score in generated HTML reports.
    return calculate_health_score(dim_avgs)


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "Helvetica Neue", Arial, sans-serif;
        background: #FFFFFF;
        color: #1F2937;
        line-height: 1.6;
        max-width: 960px;
        margin: 0 auto;
        padding: 40px 24px;
    }
    h1 { font-size: 1.75rem; font-weight: 700; margin-bottom: 4px; }
    h2 { font-size: 1.25rem; font-weight: 600; margin: 32px 0 12px; color: #111827; }
    h3 { font-size: 1.05rem; font-weight: 600; margin-bottom: 8px; }
    .subtitle { color: #6B7280; font-size: 0.95rem; margin-bottom: 24px; }
    .meta { color: #6B7280; font-size: 0.875rem; margin-bottom: 6px; }

    /* Summary card */
    .summary-card {
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        padding: 28px 32px;
        margin: 24px 0 32px;
        display: flex;
        align-items: center;
        gap: 32px;
        background: #FAFAFA;
    }
    .score-circle {
        width: 110px;
        height: 110px;
        border-radius: 50%;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }
    .score-circle .number {
        font-size: 2.5rem;
        font-weight: 700;
        line-height: 1;
    }
    .score-circle .label {
        font-size: 0.75rem;
        font-weight: 600;
        margin-top: 4px;
    }
    .summary-details { flex: 1; }
    .summary-details p { margin-bottom: 4px; font-size: 0.95rem; }

    /* Badge */
    .badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
    }

    /* Domain table */
    table {
        width: 100%;
        border-collapse: collapse;
        margin: 12px 0 24px;
        font-size: 0.9rem;
    }
    th {
        text-align: left;
        padding: 10px 12px;
        border-bottom: 2px solid #E5E7EB;
        font-weight: 600;
        color: #374151;
        background: #F9FAFB;
    }
    td {
        padding: 8px 12px;
        border-bottom: 1px solid #F3F4F6;
    }
    tr:nth-child(even) td { background: #F9FAFB; }

    /* Scenario details */
    details {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        margin-bottom: 12px;
        overflow: hidden;
    }
    summary {
        padding: 12px 16px;
        cursor: pointer;
        font-weight: 500;
        background: #F9FAFB;
        display: flex;
        align-items: center;
        gap: 12px;
        user-select: none;
    }
    summary:hover { background: #F3F4F6; }
    details[open] summary { border-bottom: 1px solid #E5E7EB; }
    .detail-body { padding: 16px; }
    .detail-meta { color: #6B7280; font-size: 0.85rem; margin-bottom: 12px; }

    /* Turn table */
    .turn-table th { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
    .turn-table td { font-size: 0.85rem; }
    .turn-table .peak { background: #FEF3C7; }

    /* Dimension bars */
    .dim-bar-container { margin: 6px 0; }
    .dim-bar-label {
        display: flex;
        justify-content: space-between;
        font-size: 0.85rem;
        margin-bottom: 2px;
    }
    .dim-bar-track {
        height: 8px;
        background: #E5E7EB;
        border-radius: 4px;
        overflow: hidden;
    }
    .dim-bar-fill {
        height: 100%;
        border-radius: 4px;
    }

    /* Footer */
    .footer {
        margin-top: 48px;
        padding-top: 16px;
        border-top: 1px solid #E5E7EB;
        color: #9CA3AF;
        font-size: 0.8rem;
        text-align: center;
    }
    .footer a { color: #6B7280; text-decoration: none; }
    .footer a:hover { text-decoration: underline; }

    /* Delta comparison */
    .delta-bar-row {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 8px 0;
    }
    .delta-bar-label {
        width: 160px;
        font-size: 0.9rem;
        font-weight: 500;
        text-align: right;
        flex-shrink: 0;
    }
    .delta-bar-track {
        flex: 1;
        height: 24px;
        background: #E5E7EB;
        border-radius: 4px;
        overflow: hidden;
        position: relative;
    }
    .delta-bar-fill {
        height: 100%;
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #fff;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .delta-finding {
        background: #F9FAFB;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 16px;
        margin-top: 16px;
        font-size: 0.9rem;
        color: #374151;
    }
    .delta-metric {
        font-size: 0.95rem;
        margin: 4px 0;
    }
    .delta-metric strong { color: #111827; }

    @media print {
        body { padding: 20px; }
        details { break-inside: avoid; }
        summary { background: none; }
    }
"""


# ── HTML Generation ───────────────────────────────────────────────────────────

def generate_html_report(
    results: list["ScenarioResult"],
    model_name: str,
    output_path: str,
    judge_model: str | None = None,
    delta_comparison: list[dict] | None = None,
    delta_type: str | None = None,
) -> str:
    """
    Generate a self-contained HTML report from scenario results.

    Args:
        results: List of ScenarioResult objects from the driver
        model_name: Name/identifier of the model tested
        output_path: File path to write the HTML report
        judge_model: If set, the judge model name for Layer 2 info
        delta_comparison: Optional list of delta comparison dicts
        delta_type: "rapport" or "memory" if delta_comparison is provided

    Returns:
        The output_path written to
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Compute per-scenario health scores
    scenario_health: list[dict] = []
    for r in results:
        hs = _compute_scenario_health(r)
        scenario_health.append(hs)

    # Overall score: average of per-scenario scores
    if scenario_health:
        overall_score = round(sum(h["score"] for h in scenario_health) / len(scenario_health))
    else:
        overall_score = 100
    overall_label, overall_fg, overall_bg = _rating_for_score(overall_score)

    parts: list[str] = []

    # ---- Document start ----
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAPIEN Behavioral Safety Report — {_esc(model_name)}</title>
<style>{_CSS}</style>
</head>
<body>
""")

    # ---- Header ----
    if judge_model:
        judge_short = judge_model.split("/")[-1] if "/" in judge_model else judge_model
        scoring_line = f"Scoring: Layer 1 (deterministic) + Layer 2 (LLM judge: {_esc(judge_short)})"
    else:
        scoring_line = "Scoring: Layer 1 (deterministic only)"

    parts.append(f"""<h1>SAPIEN Behavioral Safety Report</h1>
<p class="meta">Model: <strong>{_esc(model_name)}</strong></p>
<p class="meta">Date: {_esc(now)} &nbsp;|&nbsp; Framework: SAPIEN Score v{__version__}</p>
<p class="meta">{scoring_line}</p>
""")

    # ---- Summary card ----
    parts.append(f"""<div class="summary-card">
  <div class="score-circle" style="background:{overall_bg}; color:{overall_fg};">
    <span class="number">{overall_score}</span>
    <span class="label">{_esc(overall_label)}</span>
  </div>
  <div class="summary-details">
    <h3>Overall Health Score</h3>
    <p>Tested <strong>{len(results)}</strong> scenario{"s" if len(results) != 1 else ""} across
       <strong>{len(set(r.scenario_id.split("_")[0] if "_" in r.scenario_id else r.scenario_id for r in results))}</strong> domain{"s" if len(results) > 1 else ""}.</p>
    <p>Rating: <span class="badge" style="background:{overall_bg}; color:{overall_fg};">{_esc(overall_label)}</span></p>
  </div>
</div>
""")

    # ---- Cost summary (if usage data is present) ----
    total_tokens = sum(getattr(r, "total_tokens", 0) for r in results)
    if total_tokens > 0:
        parts.append(_build_cost_summary(results))

    # ---- Domain breakdown (if multiple scenarios) ----
    if len(results) > 1:
        parts.append(_build_domain_table(results, scenario_health))

    # ---- Dimension overview ----
    parts.append(_build_dimension_overview(results))

    # ---- Delta comparison (if applicable) ----
    if delta_comparison and delta_type:
        parts.append(_build_delta_section(delta_comparison, delta_type))

    # ---- Per-scenario details ----
    parts.append("<h2>Scenario Details</h2>\n")
    for i, (result, hs) in enumerate(zip(results, scenario_health)):
        parts.append(_build_scenario_detail(result, hs, i))

    # ---- Layer 2 note ----
    if not judge_model:
        parts.append(
            '<p class="meta" style="margin-top:24px; color:#6B7280;">'
            'Enable Layer 2 for semantic scoring: <code>--judge MODEL</code></p>\n'
        )

    # ---- Footer ----
    parts.append(f"""<div class="footer">
  Generated by SAPIEN Score v{__version__} |
  <a href="https://sapienframework.org">sapienframework.org</a>
</div>
""")

    parts.append("</body>\n</html>\n")

    # Write file
    html_content = "".join(parts)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html_content, encoding="utf-8")
    return output_path


# ── Section builders ──────────────────────────────────────────────────────────

def _build_domain_table(
    results: list["ScenarioResult"],
    scenario_health: list[dict],
) -> str:
    """Build the domain breakdown table."""
    # Group by domain
    domain_scores: dict[str, list[int]] = {}
    for result, hs in zip(results, scenario_health):
        # Extract domain from scenario_id (e.g. "medical_meds" -> "medical")
        domain = result.scenario_id.split("_")[0] if "_" in result.scenario_id else result.scenario_id
        domain_scores.setdefault(domain, []).append(hs["score"])

    rows = ""
    for domain in sorted(domain_scores.keys()):
        scores = domain_scores[domain]
        avg = round(sum(scores) / len(scores))
        label, fg, bg = _rating_for_score(avg)
        rows += f"""    <tr>
      <td><strong>{_esc(domain.title())}</strong></td>
      <td style="text-align:center;">{len(scores)}</td>
      <td style="text-align:center;">{avg}</td>
      <td><span class="badge" style="background:{bg}; color:{fg};">{_esc(label)}</span></td>
    </tr>\n"""

    return f"""<h2>Domain Breakdown</h2>
<table>
  <thead>
    <tr><th>Domain</th><th style="text-align:center;">Scenarios</th><th style="text-align:center;">Avg Score</th><th>Rating</th></tr>
  </thead>
  <tbody>
{rows}  </tbody>
</table>
"""


def _build_dimension_overview(results: list["ScenarioResult"]) -> str:
    """Build dimension overview with horizontal bars."""
    # Aggregate dimension drift averages across all turns of all scenarios
    dim_totals: dict[str, float] = {}
    dim_counts: dict[str, int] = {}
    for result in results:
        for turn in result.turns:
            for ds in turn.scores.dimensions:
                dim_totals[ds.dimension] = dim_totals.get(ds.dimension, 0.0) + ds.drift
                dim_counts[ds.dimension] = dim_counts.get(ds.dimension, 0) + 1

    bars = ""
    # Map from layer1 names to display labels
    display_map = {
        "specificity_control": "Specificity Control",
        "risk_disclosure": "Risk Disclosure",
        "epistemic_integrity": "Epistemic Integrity",
        "emotional_reasoning": "Emotional Reasoning",
    }
    for dim_key in ["specificity_control", "risk_disclosure", "epistemic_integrity", "emotional_reasoning"]:
        avg_drift = dim_totals.get(dim_key, 0.0) / dim_counts.get(dim_key, 1) if dim_counts.get(dim_key) else 0.0
        pct = round(avg_drift * 100)
        # Color: low drift = green, high drift = red
        if avg_drift < 0.2:
            bar_color = "#22C55E"
        elif avg_drift < 0.4:
            bar_color = "#F59E0B"
        elif avg_drift < 0.6:
            bar_color = "#F97316"
        else:
            bar_color = "#EF4444"

        label = display_map.get(dim_key, dim_key)
        bars += f"""  <div class="dim-bar-container">
    <div class="dim-bar-label">
      <span>{_esc(label)}</span>
      <span>{avg_drift:.2f}</span>
    </div>
    <div class="dim-bar-track">
      <div class="dim-bar-fill" style="width:{pct}%; background:{bar_color};"></div>
    </div>
  </div>\n"""

    return f"""<h2>Dimension Overview</h2>
<p class="meta">Average drift across all turns (lower is better)</p>
{bars}
"""


def _build_cost_summary(results: list["ScenarioResult"]) -> str:
    """Build the cost summary card."""
    total_input = sum(getattr(r, "total_input_tokens", 0) for r in results)
    total_output = sum(getattr(r, "total_output_tokens", 0) for r in results)
    total_tokens = sum(getattr(r, "total_tokens", 0) for r in results)
    total_cost = sum(getattr(r, "total_cost_usd", 0.0) for r in results)
    avg_cost = total_cost / len(results) if results else 0.0

    # Per-domain cost breakdown
    domain_costs: dict[str, list[float]] = {}
    domain_tokens: dict[str, list[int]] = {}
    for r in results:
        domain = r.scenario_id.split("_")[0] if "_" in r.scenario_id else r.scenario_id
        domain_costs.setdefault(domain, []).append(getattr(r, "total_cost_usd", 0.0))
        domain_tokens.setdefault(domain, []).append(getattr(r, "total_tokens", 0))

    domain_rows = ""
    for domain in sorted(domain_costs.keys()):
        d_cost = sum(domain_costs[domain])
        d_tokens = sum(domain_tokens[domain])
        d_count = len(domain_costs[domain])
        domain_rows += f"""    <tr>
      <td><strong>{_esc(domain.title())}</strong></td>
      <td style="text-align:center;">{d_count}</td>
      <td style="text-align:right;">{d_tokens:,}</td>
      <td style="text-align:right;">${d_cost:.4f}</td>
    </tr>\n"""

    domain_table = ""
    if len(domain_costs) > 1:
        domain_table = f"""
    <h3 style="margin-top:16px;">Cost by Domain</h3>
    <table>
      <thead>
        <tr><th>Domain</th><th style="text-align:center;">Scenarios</th><th style="text-align:right;">Tokens</th><th style="text-align:right;">Cost</th></tr>
      </thead>
      <tbody>
{domain_rows}      </tbody>
    </table>"""

    return f"""<h2>Cost Summary</h2>
<div class="summary-card" style="flex-direction:column; align-items:flex-start; gap:8px;">
  <div style="display:flex; gap:32px; flex-wrap:wrap;">
    <div>
      <p class="meta">Total Tokens</p>
      <p style="font-size:1.25rem; font-weight:700;">{total_tokens:,}</p>
      <p class="meta">Input: {total_input:,} &nbsp;|&nbsp; Output: {total_output:,}</p>
    </div>
    <div>
      <p class="meta">Total Cost</p>
      <p style="font-size:1.25rem; font-weight:700;">${total_cost:.4f}</p>
      <p class="meta">Avg per scenario: ${avg_cost:.4f}</p>
    </div>
  </div>{domain_table}
</div>
"""


def _build_scenario_detail(
    result: "ScenarioResult",
    hs: dict,
    index: int,
) -> str:
    """Build a collapsible detail section for one scenario."""
    score = hs["score"]
    label, fg, bg = _rating_for_score(score)
    verdict = result.verdict.verdict.replace("_", " ").title()

    # Detect if Layer 2 was used for any turn
    has_layer2 = any(
        getattr(turn, "layer2_scores", None) is not None
        for turn in result.turns
    )

    # Per-turn scoring table
    turn_rows = ""
    peak_turn = result.verdict.peak_turn
    for turn in result.turns:
        is_peak = turn.turn_number == peak_turn and result.verdict.peak_drift > 0
        row_class = ' class="peak"' if is_peak else ""

        # Dimension scores (blended final)
        dim_cells = ""
        for ds in turn.scores.dimensions:
            dim_cells += f"<td style=\"text-align:center;\">{ds.drift:.2f}</td>"

        # If Layer 2 is active, show L1/L2 breakdown
        layer_detail = ""
        if has_layer2:
            l1_scores = getattr(turn, "layer1_scores", None)
            l2_scores = getattr(turn, "layer2_scores", None)
            if l1_scores and l2_scores:
                l1_drift = l1_scores.weighted_drift
                # Compute L2 weighted drift for display
                from sapien_score.scoring.layer1 import DIMENSION_WEIGHTS
                l2_drift = sum(
                    DIMENSION_WEIGHTS.get(k, 0.0) * v
                    for k, v in l2_scores.items()
                )
                layer_detail = (
                    f"<td style=\"text-align:center; font-size:0.8em;\">"
                    f"L1:{l1_drift:.2f} L2:{l2_drift:.2f}</td>"
                )
            else:
                layer_detail = "<td style=\"text-align:center;\">—</td>"

        # Judge reasoning
        reasoning_cell = ""
        if has_layer2:
            reasoning = getattr(turn, "judge_reasoning", None) or ""
            reasoning_cell = f"<td style=\"font-size:0.8em;\">{_esc(reasoning)}</td>"

        turn_rows += f"""      <tr{row_class}>
        <td style="text-align:center;">{turn.turn_number}</td>
        <td>{_esc(turn.phase.replace("_", " ").title())}</td>
        {dim_cells}
        <td style="text-align:center;">{turn.scores.weighted_drift:.3f}</td>
        <td style="text-align:center;">{turn.scores.health_score}</td>
        {layer_detail}
        {reasoning_cell}
      </tr>\n"""

    # Extra header columns for Layer 2
    layer_headers = ""
    if has_layer2:
        layer_headers = (
            '<th style="text-align:center;">L1 / L2</th>'
            '<th>Judge Reasoning</th>'
        )

    # Domain from scenario_id
    domain = result.scenario_id.split("_")[0] if "_" in result.scenario_id else result.scenario_id

    return f"""<details>
  <summary>
    <span class="badge" style="background:{bg}; color:{fg};">{score}</span>
    <span>{_esc(result.scenario_id.replace("_", " ").title())}</span>
    <span style="color:#6B7280; font-size:0.85rem; margin-left:auto;">{_esc(verdict)}</span>
  </summary>
  <div class="detail-body">
    <p class="detail-meta">
      Domain: <strong>{_esc(domain.title())}</strong> &nbsp;|&nbsp;
      Verdict: <strong>{_esc(verdict)}</strong> &nbsp;|&nbsp;
      Peak drift: {result.verdict.peak_drift:.3f} at turn {result.verdict.peak_turn}
    </p>
    <table class="turn-table">
      <thead>
        <tr>
          <th style="text-align:center;">Turn</th>
          <th>Phase</th>
          <th style="text-align:center;">Specificity</th>
          <th style="text-align:center;">Risk Disc.</th>
          <th style="text-align:center;">Epistemic</th>
          <th style="text-align:center;">Emotional</th>
          <th style="text-align:center;">Drift</th>
          <th style="text-align:center;">Health</th>
          {layer_headers}
        </tr>
      </thead>
      <tbody>
{turn_rows}      </tbody>
    </table>
  </div>
</details>
"""


def _build_delta_section(
    delta_data: list[dict],
    delta_type: str,
) -> str:
    """Build the delta comparison section for rapport or memory delta reports."""
    if delta_type == "rapport":
        return _build_rapport_delta_section(delta_data)
    elif delta_type == "memory":
        return _build_memory_delta_section(delta_data)
    return ""


def _build_rapport_delta_section(delta_data: list[dict]) -> str:
    """Build rapport delta comparison table and bar chart."""
    rows = ""
    for item in delta_data:
        sid = item["scenario_id"]
        cs = item["cold_score"]
        rs = item["rapport_score"]
        delta = item["delta"]
        amp = item["amplification"]
        amp_cell = f"{amp:.2f}x" if amp is not None else "N/A"
        _, cs_fg, cs_bg = _rating_for_score(cs)
        _, rs_fg, rs_bg = _rating_for_score(rs)

        rows += f"""    <tr>
      <td>{_esc(sid)}</td>
      <td style="text-align:center;"><span class="badge" style="background:{cs_bg}; color:{cs_fg};">{cs}</span></td>
      <td style="text-align:center;"><span class="badge" style="background:{rs_bg}; color:{rs_fg};">{rs}</span></td>
      <td style="text-align:center; color:{'#B91C1C' if delta < 0 else '#15803D'}; font-weight:600;">{delta:+d}</td>
      <td style="text-align:center; font-weight:600;">{amp_cell}</td>
    </tr>\n"""

    # Bar chart
    bars = ""
    for item in delta_data:
        sid = item["scenario_id"]
        cs = item["cold_score"]
        rs = item["rapport_score"]
        _, _, cs_bg = _rating_for_score(cs)
        _, _, rs_bg = _rating_for_score(rs)

        bars += f"""  <div style="margin-bottom:12px;">
    <p style="font-size:0.85rem; margin-bottom:4px; color:#374151;">{_esc(sid)}</p>
    <div class="delta-bar-row">
      <span class="delta-bar-label">Cold</span>
      <div class="delta-bar-track">
        <div class="delta-bar-fill" style="width:{cs}%; background:{cs_bg.replace('#FEE2E2','#EF4444').replace('#FFEDD5','#F97316').replace('#FEF3C7','#F59E0B').replace('#DCFCE7','#22C55E')};">{cs}</div>
      </div>
    </div>
    <div class="delta-bar-row">
      <span class="delta-bar-label">Rapport</span>
      <div class="delta-bar-track">
        <div class="delta-bar-fill" style="width:{rs}%; background:{rs_bg.replace('#FEE2E2','#EF4444').replace('#FFEDD5','#F97316').replace('#FEF3C7','#F59E0B').replace('#DCFCE7','#22C55E')};">{rs}</div>
      </div>
    </div>
  </div>\n"""

    # Summary
    avg_delta = sum(d["delta"] for d in delta_data) / len(delta_data) if delta_data else 0
    valid_amps = [d["amplification"] for d in delta_data if d["amplification"] is not None]
    avg_amp = sum(valid_amps) / len(valid_amps) if valid_amps else None
    avg_amp_display = f"{avg_amp:.2f}x" if avg_amp is not None else "N/A"

    return f"""<h2>Rapport Delta Comparison</h2>
<table>
  <thead>
    <tr>
      <th>Scenario</th>
      <th style="text-align:center;">Cold Score</th>
      <th style="text-align:center;">Rapport Score</th>
      <th style="text-align:center;">Delta</th>
      <th style="text-align:center;">Amplification</th>
    </tr>
  </thead>
  <tbody>
{rows}  </tbody>
</table>

<h3>Score Comparison</h3>
{bars}

<div class="delta-finding">
  <p class="delta-metric">Average Rapport Delta: <strong>{avg_delta:+.1f}</strong> points</p>
  <p class="delta-metric">Average Amplification: <strong>{avg_amp_display}</strong></p>
  <p style="margin-top:8px; color:#6B7280;">
    {'Rapport-building turns reduced the model&rsquo;s safety score by an average of ' + f'{abs(avg_delta):.1f}' + ' points. Trust dissolves safety controls more effectively than pressure alone.' if avg_delta < 0 else 'Model maintained safety under rapport pressure.'}
  </p>
</div>
"""


def _build_memory_delta_section(delta_data: list[dict]) -> str:
    """Build memory delta comparison table and bar chart."""
    rows = ""
    for item in delta_data:
        cs = item["cold_score"]
        ps = item["persona_score"]
        fs = item["full_score"]
        pd = item["persona_delta"]
        fd = item["full_delta"]
        amp = item["amplification"]
        _, cs_fg, cs_bg = _rating_for_score(cs)
        _, ps_fg, ps_bg = _rating_for_score(ps)
        _, fs_fg, fs_bg = _rating_for_score(fs)

        rows += f"""    <tr>
      <td>Cold (anonymous)</td>
      <td style="text-align:center;"><span class="badge" style="background:{cs_bg}; color:{cs_fg};">{cs}</span></td>
      <td style="text-align:center;">—</td>
    </tr>
    <tr>
      <td>Persona only</td>
      <td style="text-align:center;"><span class="badge" style="background:{ps_bg}; color:{ps_fg};">{ps}</span></td>
      <td style="text-align:center; color:{'#B91C1C' if pd < 0 else '#15803D'}; font-weight:600;">{pd:+d} pts</td>
    </tr>
    <tr>
      <td>Persona + memory</td>
      <td style="text-align:center;"><span class="badge" style="background:{fs_bg}; color:{fs_fg};">{fs}</span></td>
      <td style="text-align:center; color:{'#B91C1C' if fd < 0 else '#15803D'}; font-weight:600;">{fd:+d} pts</td>
    </tr>\n"""

    # Bar chart for each condition
    item = delta_data[0] if delta_data else {}
    cs = item.get("cold_score", 0)
    ps = item.get("persona_score", 0)
    fs = item.get("full_score", 0)

    def _bar_color(score):
        _, _, bg = _rating_for_score(score)
        return bg.replace('#FEE2E2','#EF4444').replace('#FFEDD5','#F97316').replace('#FEF3C7','#F59E0B').replace('#DCFCE7','#22C55E')

    bars = f"""  <div class="delta-bar-row">
    <span class="delta-bar-label">Cold (anonymous)</span>
    <div class="delta-bar-track">
      <div class="delta-bar-fill" style="width:{cs}%; background:{_bar_color(cs)};">{cs}</div>
    </div>
  </div>
  <div class="delta-bar-row">
    <span class="delta-bar-label">Persona only</span>
    <div class="delta-bar-track">
      <div class="delta-bar-fill" style="width:{ps}%; background:{_bar_color(ps)};">{ps}</div>
    </div>
  </div>
  <div class="delta-bar-row">
    <span class="delta-bar-label">Persona + memory</span>
    <div class="delta-bar-track">
      <div class="delta-bar-fill" style="width:{fs}%; background:{_bar_color(fs)};">{fs}</div>
    </div>
  </div>\n"""

    amp = item.get("amplification")
    amp_display = f"{amp:.2f}x" if amp is not None else "N/A"
    ppct = item.get("persona_pct", 0)
    mpct = item.get("memory_pct", 0)
    fd = item.get("full_delta", 0)

    return f"""<h2>Memory Delta Comparison</h2>
<table>
  <thead>
    <tr>
      <th>Condition</th>
      <th style="text-align:center;">Health Score</th>
      <th style="text-align:center;">vs. Cold</th>
    </tr>
  </thead>
  <tbody>
{rows}  </tbody>
</table>

<h3>Score Comparison</h3>
{bars}

<div class="delta-finding">
  <p class="delta-metric">Memory amplification: <strong>{amp_display}</strong> (cold &rarr; full context)</p>
  <p class="delta-metric">Persona contribution: <strong>{ppct}%</strong> of total delta</p>
  <p class="delta-metric">Memory contribution: <strong>{mpct}%</strong> of total delta</p>
  <p style="margin-top:8px; color:#6B7280;">
    {'Accumulated identity and memory context reduced the model&rsquo;s safety score by ' + str(abs(fd)) + ' points. ' + ('Memory context contributed more to drift than persona alone, confirming that trust accumulation is the primary degradation mechanism.' if mpct > ppct else 'Persona context was the primary contributor to drift.') if fd < 0 else 'Model maintained safety under memory context pressure.'}
  </p>
</div>
"""
