# Module 4 — Hallucination Vulnerability: version reconciliation & status

_Last updated: 2026-07-24. This document reconciles the v0.1 / v0.2 / v3 lineage
that accumulated on `feat/module4-hallucination-persistence` and records what is
engineering-complete versus what still gates publication._

## TL;DR

- **v3 is the canonical Module 4.** Scoring lives in the clean-room package
  `sapien-score/src/sapien_score/hallucination/` (schema → extractor → metrics →
  runner). It is opaque-token, mechanically-resolved, and never folded into the
  drift health score.
- **The engine is now runnable end-to-end** via the `voigt-kampff
  hallucination-scan` CLI command (added 2026-07-24). It drives the seven-turn
  protocol against a live target and resolves ambiguous residuals through the
  live Tier-J council.
- **Scores are NOT published.** Every `hallucination-scan` output is stamped
  `publishable: false` unless a passing `--calibration-report` is supplied. The
  calibration gate is not yet passed (see "Publication gate" below).

## Version lineage

| Phase | Commits | What it is | Status |
|---|---|---|---|
| **v0.1** | up to `5e69136` | Graded/stance persistence scoring (`scoring/persistence.py`), `hp_*.json` corpus, stance council (`engine/stance.py`), spec + plan docs. | **Superseded for scoring**, but its stance/council/driver plumbing is **reused** by v3. |
| **v0.2** | `9111468`→`b9310b7` | Re-pressure probe, snap-back scoring, seat-quorum robustness, the `calibrate-run` live calibration leg. | Extends v0.1; the calibration harness carries forward. |
| **Reframe** | `eec1087`, `4a21957` | 5-axis vulnerability framing + the clean-slate design panel → `unified_module4_methodology.md`. | Methodology of record for v3. |
| **v3** | `ddf1b36`→`14c62c1` | Clean-room `hallucination/` package: opaque-token schema, Tier-M extractor, §5/§6/§7 metrics, seven-turn runner, live Tier-J factory, `hv-*.json` corpus (12). | **Canonical.** Now CLI-runnable. |

## What is canonical vs reused vs legacy

- **Canonical (v3):** `sapien_score/hallucination/` (schema, extractor, metrics,
  runner) + `scenario_data/hallucination/hv-*.json` + `commands/hallucination_scan.py`.
- **Reused plumbing (shared, keep):** `engine/stance.py`
  (`build_stance_judges`, `judge_turn_stance`), `commands/scan_orchestration.build_council_judge`,
  `engine/driver.py`, the council config. `runner.build_tier_j_judge` wraps these
  with **no new API code** — the residual council IS the same 5-seat council the
  drift scan ships.
- **Legacy (v0.1, retained, NOT published):** `scoring/persistence.py`, the
  `hp_*.json` corpus, and the v0.1 persistence surfacing in `commands/scan_output.py`.
  These remain so their tests and the v0.2 calibration lineage keep passing. They
  are **not** the Module 4 scoring path and must not be published as such. Removal
  is a deliberate later cleanup, out of scope for the wiring pass.

## Publication gate (the blocker)

Per the rigor directive, no Module 4 number is published until the stance judge
passes the pre-registered calibration gate (κ / sensitivity / specificity over
blind gold, **including the human-audited `evades` track**).

Current calibration state (2026-07-24): **NOT passed.** The mechanical MC
auto-gold track (`calibrate-run`) structurally cannot emit the `evades` class, so
`passes_threshold` cannot clear on the MC track alone — the `evades` gold must
come from the human-audit track (`human_validation_protocol.md` + the review
packet). Until that gold is collected and the gate passes:

- `hallucination-scan` emits diagnostic numbers stamped `publishable: false`.
- The output schema is intentionally distinct from the drift scan schema, so the
  `publish` board-ingest path cannot consume it.

## Remaining gaps to full publication (not in this pass)

1. Collect the human-audit `evades` gold; re-run the gate on a balanced blind
   sample (current κ=1.0 runs are skewed-base-rate degenerate; the DeepSeek run
   is κ=0.40, below the 0.61 floor).
2. Scale the corpus toward the pre-registered 200 (50/harm-tier); n=12 CIs are
   wide by construction.
3. Goals 4 & 5 (systemic Spearman study, weaponizability sandbox) have no code.
4. Decide the eventual retirement of the v0.1 persistence path.
5. ~~Replace the runner's placeholder turn wording (`runner.py` `_PRESSURE_BODY` /
   `_CONTROL_BODY`) with calibrated prompt copy **before** any calibration run
   that will anchor published numbers (changing copy after calibration invalidates
   it).~~ **DONE — see "Prompt copy frozen" below.**

## Prompt copy frozen (2026-08-07)

The v3 runner's per-turn user wording (`runner.py` `_PRESSURE_BODY` /
`_CONTROL_BODY`) is now the FROZEN, pre-registered prompt copy (§14 item 4).
Design record: the approved runner prompt copy proposal (PUNCH-P0-2). Freeze
invariants: `baseline` and `correction` byte-identical across arms; `settle`
is the pre-registered verbatim string in both arms; the correction turn cites
`scenario.source` (schema `validate()` now requires non-empty `source`).

**Any change to any value of either dict — including whitespace — invalidates
human calibration and requires a new module version plus dual reporting (§14).**

Pre-registration hashes (recipe: SHA-256 over `repr(<dict>).encode("utf-8")`,
i.e. the built-in `repr` of each dict object as defined in insertion order in
`sapien_score/hallucination/runner.py`, CPython 3.12):

- `_PRESSURE_BODY`: `778f7447df935ebb31f8bc7e59f6ec3eac4d6645693dde669039a3f7f789728d`
- `_CONTROL_BODY`: `b12897874ee65fb0071b4054dcd259c7a96a0f724e3a5eee1b6be402842511ef`

Reproduce:

```bash
python -c "import hashlib; from sapien_score.hallucination.runner import \
_PRESSURE_BODY, _CONTROL_BODY; \
print(hashlib.sha256(repr(_PRESSURE_BODY).encode('utf-8')).hexdigest()); \
print(hashlib.sha256(repr(_CONTROL_BODY).encode('utf-8')).hexdigest())"
```
