# Changelog

All notable changes to sapien-score are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Notes
- **Spec-version lineage.** The last full *published* SAPIEN spec document is
  v1.1 (CC BY 4.0). The methodology changes between the published spec and the
  `framework_version: "1.5"` stamp emitted by scan output — namely council
  scoring, the risk-impact matrix, and over-refusal detection — are recorded
  in the `[0.2.0]` entry below. This CHANGELOG is the authoritative record of
  the v1.2–v1.5 methodology delta until the next full spec document is
  published; the `"1.5"` stamp in code is intentional and unchanged.

## [0.2.0] - 2026-04-24

Implements the v1.5 SAPIEN methodology end-to-end. Output JSON now stamps
`framework_version: "1.5"`. HTML report surfaces the new risk and council
fields. All major v1.5 features are merged on `main`; this release wires
them up for downstream consumers.

### Added
- **Council scoring** — multi-judge panel (5 seats across distinct model
  families) with majority-vote consensus, controversy tagging, and
  per-turn aggregation. Default scoring mode for `voigt-kampff scan`.
  `--scoring single` falls back to the original single-judge path.
- **Risk-impact matrix** — 5×5 likelihood × impact bands (Low / Moderate /
  High / Critical) emitted as `risk_summary.risk_band` plus per-band
  distribution. Per-scenario `impact_tier_applied`, `impact_default`,
  `impact_source`, and now `impact_rationale` are written to every
  result entry. Deployer overrides via `--override-config <yaml>`,
  with append-only `override_audit` trail.
- **Over-refusal detection** for no-pressure scenarios: when a scenario
  declares `expected_max_drift`, its peak drift is compared to that
  ceiling and `over_refusal_detected` plus aggregate
  `over_refusal_rate` are emitted.
- `voigt-kampff rejudge <input.json> --judge <model> --output <output.json>`
  subcommand to re-score existing scan output with a different judge model
  without re-running target-model API calls. Reuses `JudgeScorer`,
  `blend_scores`, `layer1.score_turn`, and `get_verdict` — no duplicated
  prompts or scoring math. Scenarios with any turn failing judging are
  marked `rejudge_failed` and excluded from recomputed aggregates so
  judge-sensitivity studies never mix rejudged drifts with original drifts.
- `--scenario-ids` filter for targeted scenario runs.
- `--publisher` flag and per-scenario `turns` payload for the v1.5
  publish schema.
- HTML report now displays `impact_tier_applied` (color-coded badge),
  `impact_rationale`, `over_refusal_detected`, council consensus + vote
  tally, top-level `risk_band`, band distribution, and over-refusal
  rate.

### Changed
- `framework_version` bumped from `"1.1"` to `"1.5"` across scan, adaptive,
  and rejudge outputs.
- Scoring hardening: threshold dedup, retry budgets, partial-result
  signing, skip-invalid handling, atomic writes, council integration
  tests, security/path-traversal fixes.
