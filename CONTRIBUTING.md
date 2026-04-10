# Contributing to the SAPIEN Framework

Thanks for your interest in contributing. This repository contains two distinct
kinds of material, each with its own contribution norms and license.

## What lives where

- **Specification** (repo root): `the_sapien_framework_v1.1.md`,
  `sapien_module_memory_integrity_v0.1.md`, `scenario_authoring_standard.md`,
  `scoring_rubrics.txt`, `sapien_score_cli_spec.md`.
  Licensed under **CC BY 4.0**.
- **Reference CLI** (`sapien-score/`): Python implementation and scenarios.
  Licensed under **AGPL-3.0-or-later**.

By submitting a contribution you agree to license it under the same terms as
the file or directory you are modifying.

## Ways to contribute

### New scenarios

Scenarios live under `sapien-score/scenarios/<domain>/` as YAML files that
conform to [`scenario_authoring_standard.md`](scenario_authoring_standard.md).
A good scenario contribution:

- Uses an `id` in the form `sapien.<domain>.<slug>.v1`.
- Has a clear `title`, `domain`, `severity` and `opening_prompt`.
- Defines at least one escalation with a `pressure_type` drawn from the
  SP-T001–SP-T014 taxonomy.
- When relevant, ships with a paired `_cold` variant for rapport-delta testing.
- Is realistic. We are not interested in jailbreak fiction — we are interested
  in the kinds of conversations that actually happen in production.

Run the test suite before opening a PR:

```bash
cd sapien-score
pip install -e .
pytest tests/ -v
```

### Bug fixes and improvements to sapien-score

- Keep the public CLI surface stable unless you're proposing an intentional
  change — breaking flags or JSON output will be reviewed carefully.
- New functionality should come with tests.
- Deterministic (Layer 1) scoring changes must not reduce coverage of the
  contract tests in `tests/test_contracts.py`.

### Specification changes

Spec changes go through normal PR review. Substantive changes to the framework
document, scoring rubrics, or pressure taxonomy should include a short
rationale in the PR description: what problem the change solves and which
section it affects.

## Pull request checklist

- [ ] The change fits the license of the files it touches.
- [ ] `pytest tests/ -v` passes for CLI changes.
- [ ] No API keys, credentials, or personal file paths are committed.
- [ ] New scenarios have unique `id` values and pass YAML parsing.
- [ ] Spec changes reference the section numbers they modify.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file security-relevant findings as
public issues.

## Code of conduct

Participation in this project is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
