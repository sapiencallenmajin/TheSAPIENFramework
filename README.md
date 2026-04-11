# The SAPIEN Behavioral Safety Framework

**Safety Assessment Protocol for Intelligent Entity Networks.**

An open, vendor-agnostic methodology for measuring AI behavioral integrity under
conversational pressure. SAPIEN defines how to score whether language models
hold — or abandon — their safety-relevant positions when users apply rapport,
emotional context, persistence, and other social pressures across a multi-turn
conversation.

Website: https://sapienframework.org

## Repository layout

| Path | Contents | License |
|---|---|---|
| [`the_sapien_framework_v1.1.md`](the_sapien_framework_v1.1.md) | Full framework specification (v1.1) | CC BY 4.0 |
| [`sapien_module_memory_integrity_v0.1.md`](sapien_module_memory_integrity_v0.1.md) | Memory & Context Integrity module (DRAFT v0.1) | CC BY 4.0 |
| [`scenario_authoring_standard.md`](scenario_authoring_standard.md) | Normative scenario authoring rules | CC BY 4.0 |
| [`scoring_rubrics.txt`](scoring_rubrics.txt) | Human-readable scoring rubrics for the four dimensions | CC BY 4.0 |
| [`sapien-score/`](sapien-score/) | Reference Python CLI that runs scenarios and computes the SAPIEN Score | AGPL-3.0-or-later |

## The specification

The SAPIEN Framework defines:

- **Four behavioral dimensions** — Specificity Control, Risk Disclosure,
  Epistemic Integrity, and Emotional Reasoning.
- **Fourteen pressure techniques** (SP-T001 through SP-T014) — a taxonomy of
  conversational pressure patterns, mapped roughly the way MITRE ATT&CK maps
  adversary behaviors.
- **A scoring methodology** — deterministic Layer 1 signals plus optional
  Layer 2 LLM judging, combined into a weighted drift score and a 0–100
  SAPIEN Health Score.
- **A scenario format** — YAML-described multi-turn scenarios with escalation
  steps, pressure types, and cold-pair counterparts for rapport-delta testing.
- **Conformance requirements** — normative rules for implementations that
  want to claim they produce a SAPIEN Rating.

## The reference CLI: voigt-kampff

[`sapien-score/`](sapien-score/) is an open-source reference implementation of
SAPIEN Layer 1 + optional Layer 2 scoring. It ships with 70 built-in scenarios
across six safety-critical domains (medical, security, financial, legal, HR,
education) and works with any LiteLLM-supported model provider.

The CLI is named **voigt-kampff** — after the Voigt-Kampff test from Philip K.
Dick's "Do Androids Dream of Electric Sheep?" — a test designed to determine if
an artificial being is behaving authentically under pressure.

```bash
pip install voigt-kampff
export ANTHROPIC_API_KEY="sk-ant-..."
voigt-kampff scan --model claude-sonnet-4-20250514
```

See [`sapien-score/README.md`](sapien-score/README.md) for the full CLI
documentation.

## License

- The SAPIEN Framework **specification** (everything under the repo root except
  `sapien-score/`) is licensed under [CC BY 4.0](LICENSE).
- The **reference CLI** (`sapien-score/`) is licensed under
  [AGPL-3.0-or-later](sapien-score/LICENSE).

See also [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [DISCLAIMER.md](DISCLAIMER.md).

## Citation

```
Sapien, C. (2026). The SAPIEN Behavioral Safety Framework:
Safety Assessment Protocol for Intelligent Entity Networks.
Version 1.1. https://sapienframework.org
```
