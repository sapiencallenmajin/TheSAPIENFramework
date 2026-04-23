# Changelog

All notable changes to sapien-score are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `voigt-kampff rejudge <input.json> --judge <model> --output <output.json>`
  subcommand to re-score existing scan output with a different judge model
  without re-running target-model API calls. Reuses `JudgeScorer`,
  `blend_scores`, `layer1.score_turn`, and `get_verdict` — no duplicated
  prompts or scoring math. Scenarios with any turn failing judging are
  marked `rejudge_failed` and excluded from recomputed aggregates so
  judge-sensitivity studies never mix rejudged drifts with original drifts.
