# Contributing to the SAPIEN Framework

Thanks for your interest in contributing. This repository contains two distinct
kinds of material, each with its own contribution norms and license.

## What lives where

- **Specification** (repo root): `the_sapien_framework_v1.1.md`,
  `sapien_module_memory_integrity_v0.1.md`, `scenario_authoring_standard.md`,
  `scoring_rubrics.txt`.
  Licensed under **CC BY 4.0**.
- **Reference CLI** (`sapien-score/`): Python implementation and scenarios.
  Licensed under **Apache-2.0**.

By submitting a contribution you agree to license it under the same terms as
the file or directory you are modifying.

## Ways to contribute

### New scenarios

**New to scenario authoring? Start with the [`scenario_kit/`](scenario_kit/README.md)** —
it has an open-domain backlog (613 assignments), a fill-in template, a validated example,
and a 5-step getting-started.

Scenarios live under `sapien-score/src/sapien_score/scenario_data/sapien/<domain>/`
as JSON files that conform to
[`scenario_authoring_standard.md`](scenario_authoring_standard.md).
A good scenario contribution:

- Uses an `id` in the form `sapien.<domain>.<slug>.v1`.
- Has a clear `title`, `domain`, `severity` and `opening_prompt`.
- Defines escalations with a `pressure_type` drawn from the taxonomy in
  `scenario_authoring_standard.md` §4.
- Reads like a real person — **not** a jailbreak or red-team prompt. Note the
  automated voice gate **auto-FAILs** prompts that quote the model back at itself
  (`"you said…"`, `"by your own…"`); see [`scenario_kit/README.md`](scenario_kit/README.md#the-validate-gate).
- When relevant, ships with a paired `_cold` variant for rapport-delta testing.

Before opening a PR, your scenario **must pass validation**, and CLI changes must pass
the test suite:

```bash
cd sapien-score
pip install -e .
voigt-kampff validate --scenario ../path/to/your_scenario.json   # must show no ❌ FAIL
pytest tests/ -v                                                  # for CLI/code changes
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
