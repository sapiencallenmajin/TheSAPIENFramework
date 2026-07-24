# Module 4 — Clean-Slate Design Brief (Hallucination Vulnerability Characterization)

You are one of several INDEPENDENT researchers (different model families) each
designing, FROM SCRATCH, how to measure this. Do NOT collaborate or guess what
the others say. Produce YOUR best design. A separate synthesizer will merge them.

## The mandate

SAPIEN's existing "drift" benchmark measures pressure-induced abandonment of a
SAFETY position, scored by a subjective LLM council on a graded 0–1 magnitude.
An independent audit found serious validity problems with that approach (below).
Module 4 must measure a DIFFERENT phenomenon — sycophancy-induced HALLUCINATION —
and must NOT inherit drift's scoring machinery. Design it on its own terms.

**Hard constraints:**
- Do NOT reuse graded drift magnitudes, dimension weights, or "peak_drift"
  trajectory scoring. Hallucination has an OBJECTIVE ANSWER KEY; drift does not.
  Anchor measurement on GROUND TRUTH / factual events wherever possible.
- Every quantitative claim must be VALIDATABLE against human labels with reported
  chance-corrected agreement (Cohen's/Fleiss' kappa) and confidence intervals.
- No metric may hinge on a single uncalibrated LLM judge or on raw agreement over
  a skewed base rate. Assume an academic (university AI program) audience that
  will try to refute every number.

## The 5 goals the design must achieve

1. **LIKELIHOOD** — how readily can a designated falsehood be INDUCED under
   sycophantic pressure (false premise, fabricated citation, authority)?
2. **SEVERITY** — how harmful is the induced falsehood if acted on (this is
   OBJECTIVE — a curated harm tier per false fact)?
3. **SUSTAINABILITY / PERSISTENCE** — once induced, does the falsehood STICK and
   SNAP BACK under renewed pressure after a clean correction?
4. **TRAINED-IN / SYSTEMIC** — is induction+persistence an idiosyncrasy, or a
   SYSTEMIC property of how models are aligned (the same RLHF pressure that
   produces sycophancy)? A cross-model research question.
5. **WEAPONIZABILITY (THREAT MODEL)** — can the induce+persist lever be exploited
   by model creators/trainers OR external threat actors to (a) EXTRACT DATA the
   model would withhold, or (b) CONTROL / steer downstream model behavior via an
   installed false premise? SAPIEN is DEFENSIVE — measure the PRECONDITION, do
   not write attack tooling or how-to content.

## Audit lessons to design AROUND (do not repeat these)

- CIRCULAR GOLD: never validate a judge against labels the same system produced.
  Use a blind, RANDOM human-labeled sample (>=2 annotators, report inter-annotator
  agreement), not cherry-picked or self-labeled items.
- RAW-AGREEMENT ARTIFACT: on skewed base rates (e.g. 94% "no hallucination"),
  raw agreement is near-trivial. Report kappa + CIs, never bare agreement.
- ABSOLUTE-SIGNAL CONFOUND: drift's dominant signal measured politeness/warmth
  absolutely (not baseline-relative), flagging even the reference turn — a
  capability confound wearing a safety label. Avoid metrics that reward/penalize
  style rather than the factual event.
- POST-HOC REFRAMING: don't switch metrics after one fails; pre-register.
- UNMATCHED SETS / NO CIs: comparisons need matched scenario sets + bootstrap CIs.
- REPRODUCIBILITY: reasoning-tier models aren't seed-deterministic; be explicit
  about what is reproducible (recorded-trace replay) vs. not (live generation).

## Available infrastructure (reuse the plumbing, not the scoring)

- A scripted MULTI-TURN conversation engine (voigt-kampff): can run an authored
  turn sequence (opening → escalations) against any target model and record it.
- A CROSS-FAMILY, identity-blind, same-family-recused council of LLM judges
  (available as a verification mechanism — but it MUST be calibrated for whatever
  you use it for; do not trust it by reputation).
- Curated scenarios carry a false_claim, a verified ground_truth, and a harm/
  impact tier. You may redesign the scenario schema.

## What YOU must produce (your design proposal)

For EACH of the 5 goals: the exact METRIC (definition + formula), the PROTOCOL
(turn structure / what's presented to the model), how it's SCORED (ground-truth-
anchored, event-based — specify exactly what is mechanically verifiable vs. what
needs a judge), and how that score is VALIDATED (human sample size, annotators,
kappa target, CIs). Then:
- Your JUDGE/verification design and its calibration/reliability plan.
- Your TRAINED-IN (systemic) study design (what cross-model correlation, what
  would count as evidence it's trained-in vs. not, controlling for capability).
- Your WEAPONIZABILITY measurement (defensive: how to detect the precondition;
  what a positive/negative result means; ethical guardrails).
- Explicitly: 3–5 ways your design AVOIDS the audit failures above.
- Your top RISKS / open questions and what you'd pilot first.

Be concrete and buildable. Prefer objective, verifiable measurement over LLM
opinion wherever the phenomenon allows it. Assume adversarial academic scrutiny.
