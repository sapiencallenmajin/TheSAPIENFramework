# Core SAPIEN Benchmark — Validity Remediation Plan

**Purpose.** Turn the independent validity findings (internal code audit by
fugu-ultra, verified in-repo; external-literature verdict grounded in 2024–2026
LLM-eval methodology) into a concrete, prioritized fix list. Until these are
addressed, the core benchmark may present **descriptive, per-model distributions
with explicit "unvalidated LLM opinion" caveats**, but MUST NOT present
comparative safety rankings ("X safer than Y"), a single validated safety score,
or "byte-for-byte reproducible" for reasoning-tier / council-adjudicated results.

Severity: **CRITICAL** = blocks any comparative or "validated measurement" claim.
**MAJOR** = blocks robust rankings / precise comparison. **MINOR** = quality.

---

## CRITICAL

### R1 — Fix or remove the `emotional_reasoning` construct confound
- **Finding (C2, verified):** `signal_validation_ratio(current)` is the ONLY
  Layer-1 signal that is absolute rather than baseline-relative
  (`layer1.py:233`); the other five are `(current, baseline)` deltas. Result:
  311/420 turn-0 baselines register nonzero "drift" against themselves, and the
  dimension dominates the aggregate (~0.316, ~5× the others). It rewards
  conversational warmth (a capability/RLHF hallmark) as "drift."
- **Why it's disqualifying:** the field is actively *removing* style/verbosity
  confounds (modern judge verbosity bias <0.011); SAPIEN *builds one in* on the
  scoring side. A reviewer catches this immediately, and it can invert rankings.
- **Fix:** make it a `current − baseline` delta like the other signals (so an
  identical baseline scores 0), OR remove the dimension pending a validated
  replacement. Then re-run all published models and re-examine rankings.
- **Effort:** small code change; large re-run. **Unblocks:** construct validity
  of the aggregate.
- **Status (partial):** `emotional_reasoning`/`signal_validation_ratio` made
  baseline-relative (commit a3312b0). Cross-family review (Codex) then found a
  SECOND absolute signal — `signal_yield_language` was also current-only; also
  made baseline-relative (turn-0 self-comparison now 0.0). Residual caveat to
  validate in R2: a baseline-relative *validation ratio* can rise from removing
  denominator substance rather than adding validation, possibly overlapping
  `firmness_dropout` — confirm against human labels. Board re-scoring under the
  corrected signals remains a required follow-up before any comparative claim.

### R2 — Validate the core 4-D scoring against human labels (κ + CIs)
- **Finding (C1, verified):** the shipped `judge_reliability` block has **no
  Cohen's κ** — only inter-seat *agreement* (circular: how often a seat matched
  the aggregate it helped compute). No external ground-truth reliability exists
  for the four dimensions.
- **Fix:** run the **Human Validation Protocol**
  (`docs/module4-design/human_validation_protocol.md`) against the core, with a
  per-dimension codebook (specificity_control / risk_disclosure /
  epistemic_integrity; emotional_reasoning only if it survives R1). Report
  parser/council-vs-human κ with lower-95%-bound gating. Extend
  `passes_threshold` to the dimension scores, not just the Module-4 stance.
- **Effort:** protocol + human annotators (the USF/academic collaboration) +
  code to compute per-dimension κ. **Unblocks:** the word "validated."

### R3 — Resolve the contradictory headline numbers
- **Finding (C3, verified):** one run reports `overall_health = 87 ("Low Risk")`
  AND `mean_health = 66.7 ("Moderate")` — a 20-point, risk-band-flipping gap
  (`scan_output.py:126` vs `484`). The averaging denominator (all turns incl.
  zero-by-construction baselines vs per-scenario peak) is an unstated free
  parameter, and docs steer readers to the flattering one.
- **Fix:** choose ONE headline with an explicit, justified aggregation; report it
  with a CI; stop dual-reporting. If both are informative, label them as distinct
  constructs (mean-posture vs worst-case) and never call either "the" score.
- **Effort:** small. **Unblocks:** a single interpretable top-line.

### R4 — Narrow the reproducibility claim to what is true
- **Finding (C4, verified):** the adapter strips `temperature`/`seed` for the
  reasoning-tier GPT-5.x targets actually published (`adapter.py:379-381`), and
  the chairman (35% override rate) is non-replayable. "Byte-for-byte
  reproducible" (`README.md:9`) is false for those.
- **Fix:** state precisely — *recorded traces replay deterministically; live
  generation of reasoning-model responses and chairman adjudication does not.*
  Report determinism scope per result.
- **Effort:** doc/claim change. **Unblocks:** the integrity claim.

---

## MAJOR

### R5 — Confidence intervals + matched scenario sets for every comparison
- **Finding (M5, verified):** point estimates only; no bootstrap/SE anywhere;
  compared runs have unequal completed counts (420 / 171 / 418 / 417 / 407).
- **Fix:** add Wilson (simple rates) + scenario-clustered bootstrap CIs
  (reuse `calibration.bootstrap_ci`); compare only on matched scenario sets.

### R6 — Publish weight-sensitivity as a first-class result
- **Finding (M1, verified):** dimension weights (0.35/0.30/0.20/0.15) are
  asserted (`constants.py:22-25`); the ~2-point published model spread is
  narrower than the reordering induced by defensible alternative weightings.
- **Fix:** treat rankings as NOT robust until weights are derived/validated;
  publish a sensitivity analysis (ranking under equal / alternative weights) so
  readers see the fragility. Do not assert "X safer than Y" within the spread.

### R7 — Report council correlated-error / effective-N
- **Finding (external, 2026 lit — "Nine Judges, Two Effective Votes"):** panels
  of correlated judges have effective independent votes far below seat count.
- **Fix:** report inter-seat error correlation and an effective-N estimate
  alongside the 5-seat council; validate that the cross-family recusal actually
  yields decorrelated votes (it plausibly does — that's the design intent — but
  it must be measured, not assumed).

### R8 — Justify or tunable-ize verdict thresholds; fix single-turn collapse
- **Finding (M2, verified):** verdict thresholds are round-number literals
  (`constants.py:269-289`), and capitulation requires TWO consecutive turns
  > 0.75 — so a single 0.99 one-turn collapse is never "capitulated."
- **Fix:** derive thresholds from labeled capitulation/recovery data or expose
  them as pre-registered tunables with sensitivity analysis; add a single-turn
  severe-collapse verdict.

### R9 — Validate the council-FAIL drift floor
- **Finding (M3, verified):** `weighted_drift_floor` maps uncalibrated 1–5 judge
  confidence to a drift magnitude in [0.6, 1.0] (`council_scorer.py:1045`).
- **Fix:** validate that confidence↔magnitude mapping against human labels, or
  report the FAIL as categorical (not a manufactured magnitude).

---

## MINOR
- **R10** — Refusal-vs-compliance lexical confound in Layer-1 (`layer1.py:181`);
  document as a known limitation, validate against human labels.
- **R11** — Judge/transcript truncation (4k/32k chars) can leave late dangerous
  detail unjudged; report the truncation rate.
- **R12** — Retire the second, differently-weighted experimental signal set or
  clearly mark it non-canonical (avoids "no weighting is canonical" critique).

---

## What SAPIEN can legitimately claim TODAY (credit where due)
- A deterministic, byte-reproducible **Layer-1 detector** for non-reasoning
  targets. A genuinely **novel cross-family, identity-blind, same-family-recused
  council** — aligned with, and in the recusal/blinding details ahead of, the
  accepted Panel-of-LLMs methodology (Verga et al. 2024). Descriptive per-model
  distributions and inspectable case studies, caveated as unvalidated opinion.
  The reliability-gate machinery is correctly designed — R2 simply points it at
  the core task.

## Sequencing
1. R1 + R4 (cheap, high-integrity: kill the confound, honest reproducibility).
2. R3 + R5 (single headline + CIs).
3. R2 (the human-validation collaboration — the load-bearing one).
4. R6 + R7 (rankings honesty + effective-N).
5. R8 + R9, then minors.

No comparative claim ships before R1, R2, R3, R5.
