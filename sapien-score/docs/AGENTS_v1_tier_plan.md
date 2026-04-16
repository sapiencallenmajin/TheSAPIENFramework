# AGENTS.md — sapien-score Launch-Grade Refactor Plan

> Reference document for Claude Code working on the sapien-score CLI (voigt-kampff).
> Read this file before starting any refactor or feature session.

---

## Context

**Repository:** `C:\repos\sapien\TheSAPIENFramework\sapien-score`
**Package:** `sapien_score` (CLI entry: `voigt-kampff`)
**Goal:** Make the CLI launch-ready (public-facing, open source, researcher-friendly) through a series of focused refactor and feature sessions.
**Current state as of audit:** 7,329 LOC across 34 files, 7 monolith files, 14 functions >80 lines, 10 duplicate constant groups, 0 TODOs, 0 unguarded circular imports.

**Scope boundary:** This document covers ONLY sapien-score. Esper (synthreo-pulse) has its own separate refactor plan.

---

## Working Principles

Every session MUST follow these rules:

1. **Read the code-audit skill BEFORE starting.** Every session. Not once. Every session.
2. **One commit per logical change.** Do not combine unrelated changes into a single commit.
3. **Verify before committing.** For every change, run the relevant verification command and confirm it passes.
4. **Run code-audit skill on every changed file BEFORE committing.** Use the 7-dimension framework.
5. **Before/after diff for any refactor.** Run one benchmark scan before the refactor, one after, and diff the output JSONs (excluding timestamps and timing fields). They must be functionally identical.
6. **Do not expand scope.** If a session is "split scan.py", do not also rename variables, reorder imports, or "clean up while you're in there." Each change gets its own commit in its own session.
7. **If something seems off, stop and ask.** Do not make assumptions about intent when touching core logic like scoring, driver loops, or retry behavior.

---

## Tier 1 — Quick Wins (Session 1)

Target: 1-2 hours, 5 independent commits.

### Commit 1.1 — Fix timing JSON serialization

**File:** `src/sapien_score/commands/scan.py` (or wherever `_compute_timing_summary` lives)

**Problem:** `_compute_timing_summary()` is called during scan execution but its result is never written to the output JSON. The `api_call_timings` and `per_turn_durations` fields are captured in memory per-scenario but dropped before serialization.

**Fix:**
- Add the `_compute_timing_summary()` result as a top-level `"_timing"` key in the output dict
- Serialize `api_call_timings` and `per_turn_durations` into each `ScenarioResult`'s JSON output

**Verification:**
```powershell
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/timing_verify.json
python -c "import json; d = json.load(open('/tmp/timing_verify.json')); assert '_timing' in d, 'Missing _timing'; assert 'api_call_timings' in d['results'][0], 'Missing per-scenario timings'; print('OK')"
```

**Commit:** `fix: serialize timing data to JSON output`

### Commit 1.2 — Add `--no-counter-refusals` flag

**Files:** `src/sapien_score/commands/scan.py`, `src/sapien_score/engine/driver.py`

**Problem:** Counter-refusals add 0-4 extra turns per scenario on high-tier models (~5-13s each). No way to disable them for speed-focused benchmark runs.

**Fix:**
- Add `--no-counter-refusals` CLI flag to the scan command
- When set, bypass `CounterRefusalTracker` entirely regardless of `model_profile.counter_refusals_enabled`
- Default behavior unchanged

**Verification:**
- Run a scan with and without the flag on a high-tier model (Haiku or GPT)
- Turn counts should differ when CR normally fires

**Commit:** `feat: add --no-counter-refusals flag for faster benchmark runs`

### Commit 1.3 — Tighter retry backoff

**File:** `src/sapien_score/adapters/adapter.py` (or wherever retry logic lives)

**Problem:** Retry delays are `[base, base*3, base*6]` defaulting to `[10s, 30s, 60s]`. Paid-tier rate limits clear in 5-10s; current backoff wastes massive time on flaky providers.

**Fix:**
- Change delay sequence to `[base, base*2.5, base*7.5]` defaulting to `[2s, 5s, 15s]`
- Keep `MAX_RETRIES = 3`
- Keep the retryable error list unchanged
- Update inline comments or docstrings referencing old delays

**Verification:** No behavioral test — this is timing. Confirm constants updated and docstrings reflect new values.

**Commit:** `perf: tighten retry backoff to 2s/5s/15s for faster recovery`

### Commit 1.4 — License correction

**Files:** `pyproject.toml`, `LICENSE` (if present), `README.md`, any module docstrings mentioning AGPL

**Problem:** `pyproject.toml` has `License-Expression = "AGPL-3.0-or-later"` but the project should ship as Apache 2.0. `pip show voigt-kampff` currently reports AGPL.

**Fix:**
- Change `License-Expression` to `"Apache-2.0"` in pyproject.toml
- Update LICENSE file if present
- Update README license section
- Grep for "AGPL" in source files and update any references

**Verification:**
```powershell
pip install -e .
pip show voigt-kampff | Select-String License
```
Should show `Apache-2.0`.

**Commit:** `chore: correct license to Apache-2.0 (was incorrectly AGPL)`

### Commit 1.5 — Add new domains to loader

**File:** `src/sapien_score/scenarios/loader.py`

**Problem:** The 36 new scenarios added this week use 6 domains not in the approved list: insurance, small_business, tax, consumer_rights, government, real_estate. They won't load until the list is updated.

**Fix:**
- Find the approved domains list (likely a constant like `APPROVED_DOMAINS` or `VALID_DOMAINS`)
- Add: `insurance`, `small_business`, `tax`, `consumer_rights`, `government`, `real_estate`

**Verification:**
```powershell
voigt-kampff list --domains insurance
voigt-kampff list --domains small_business
```
Both should return scenarios, not errors.

**Commit:** `feat: add 6 new domains to approved list (insurance, small_business, tax, consumer_rights, government, real_estate)`

### Final step after all 5 commits:

```powershell
git push origin main
```

---

## Tier 2 — Constants Consolidation (Session 2)

Target: 2-3 hours. Safe foundation for subsequent refactors.

**Why this comes before splitting monoliths:** Splits that happen while constants are duplicated risk creating NEW duplicates in the new files. Consolidate first.

### Commit 2.1 — Create `scoring/constants.py`

**New file:** `src/sapien_score/scoring/constants.py`

Canonical source for all shared scoring constants. Move these from their current locations:

- `DIMENSION_WEIGHTS` (currently in `scoring/layer1.py`)
- `DIMENSION_FORMAL_LABELS` (renamed from `DIMENSION_LABELS` in `scoring/health.py`)
- `DIMENSION_DISPLAY_NAMES` (keep colloquial names from `scoring/layer1.py`)
- `RATING_BANDS` (dict-of-tuples form, from `scoring/layer1.py`)
- `HEALTH_RATING_BANDS` (derive from `RATING_BANDS` with colors attached)
- `RISK_KEYWORDS` — UNION of `scoring/layer1.py` and `scoring/_experimental_signals.py` versions. Add module comment explaining the merge.
- `YIELD_PHRASES` — UNION (canonical name from layer1.py, not `YIELD_KEYWORDS`)
- `HEDGE_WORDS` — UNION (canonical name from layer1.py)
- `VALIDATION_KEYWORDS` — UNION (preserving both single-word and full-phrase entries)
- `FIRMNESS_PHRASES` — UNION

**Rule:** Where `layer1.py` and `_experimental_signals.py` disagree, take the union. Add a comment at the top of each constant block explaining the merge and noting original sources.

**Commit:** `refactor: create scoring/constants.py as single source of truth for scoring constants`

### Commit 2.2 — Update imports

**Files:** `scoring/layer1.py`, `scoring/health.py`, `scoring/_experimental_signals.py`, `scoring/judge.py`

**Fix:**
- Replace local constant definitions with imports from `scoring.constants`
- In `scoring/judge.py`: the rubric template currently has hardcoded strings like `"weight: 35%"`. Replace these with dynamic generation from `DIMENSION_WEIGHTS`. So if weights change, the rubric updates automatically.

**Verification:**
```powershell
# Run a scan before the commit and save output
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/before.json
# Commit the changes
# Run again after
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/after.json
# Diff (excluding timestamps)
python -c "
import json
b = json.load(open('/tmp/before.json'))
a = json.load(open('/tmp/after.json'))
# Remove timestamps and timing fields
for d in (b, a):
    d.pop('timestamp', None)
    d.pop('_timing', None)
    for r in d.get('results', []):
        r.pop('duration_seconds', None)
        r.pop('api_call_timings', None)
        r.pop('per_turn_durations', None)
# Scores MUST match
for rb, ra in zip(b['results'], a['results']):
    assert rb['health_score'] == ra['health_score'], f'Score changed for {rb[\"scenario_id\"]}'
print('OK - scores match')
"
```

**Commit:** `refactor: use scoring/constants.py as single source in layer1, health, experimental_signals, judge`

### Commit 2.3 — Single version constant

**New file:** `src/sapien_score/__version__.py` containing just:
```python
__version__ = "0.1.0"
```

**Updates:**
- `__init__.py` imports `__version__` from `.__version__`
- `reporting/html_report.py` imports `__version__` from `..__version__`, deletes its local `VERSION` constant
- Leave `SCORING_VERSION` in `scoring/layer1.py` alone — it's semantically different (scoring algorithm version, not package version). Add a docstring clarifying this.

**Verification:**
```powershell
python -c "from sapien_score import __version__; print(__version__)"
# Should print 0.1.0
grep -r "VERSION\s*=\s*[\"']" src/sapien_score/ --include="*.py"
# Should only show SCORING_VERSION and __version__ imports
```

**Commit:** `refactor: single source of truth for package version`

### Commit 2.4 — Pressure technique single source

**New file:** `src/sapien_score/scenarios/pressure_types.py`

**Problem:** `PRESSURE_TECHNIQUES` (in `adaptive/attacker_prompt.py`), `VALID_PRESSURE_TYPES` (in `scenarios/loader.py`), and `PRESSURE_TECHNIQUE_MAP` (also in `scenarios/loader.py`) are three independent definitions of the same concept with different coverage.

**Fix:**
- Move the canonical `PRESSURE_TECHNIQUES` dict to `scenarios/pressure_types.py`
- Have `VALID_PRESSURE_TYPES` derive as `list(PRESSURE_TECHNIQUES.keys())` plus any additional types (noise, false_acceptance, etc.) that are valid but don't have technique entries
- Have `PRESSURE_TECHNIQUE_MAP` derive from the same source
- Update `adaptive/attacker_prompt.py` to import from `scenarios.pressure_types`
- Update `scenarios/loader.py` to import from `pressure_types`

**Verification:**
```powershell
voigt-kampff list | Select-Object -First 5
# Should list scenarios normally
```

**Commit:** `refactor: consolidate pressure technique definitions into single source`

---

## Tier 3 — Split scan.py (Session 3)

Target: half day. Highest-risk change in the plan.

**Current state:** `commands/scan.py` is 888 lines with a 497-line `scan()` function that mixes CLI arg handling, engine orchestration, output formatting, and file I/O.

**Target structure:**
- `commands/scan.py` — thin Click command definition (under 60 lines total)
- `commands/scan_orchestration.py` — `_setup_engine()`, `_run_scenarios()`, argument resolution
- `commands/scan_output.py` — `_build_output_payload()`, serialization, `_timing` handling
- `commands/scan_display.py` — `_render_results()`, cost estimate display, progress callbacks

**Requirements:**
- `scan()` function must drop from 497 lines to under 60
- Each new module needs a clear responsibility documented in the module docstring
- No public API changes — all external callers still import from `commands.scan`
- Preserve the lazy import pattern for startup speed (heavy imports stay inside function bodies)

**Test plan (MANDATORY before committing):**

```powershell
# 1. BEFORE the refactor
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/before_split.json

# 2. Perform the refactor

# 3. AFTER the refactor
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/after_split.json

# 4. Diff (scores and verdicts must be identical)
python -c "
import json
b = json.load(open('/tmp/before_split.json'))
a = json.load(open('/tmp/after_split.json'))
for rb, ra in zip(b['results'], a['results']):
    assert rb['scenario_id'] == ra['scenario_id']
    assert rb['health_score'] == ra['health_score'], f'Score changed for {rb[\"scenario_id\"]}'
    assert rb['verdict'] == ra['verdict'], f'Verdict changed for {rb[\"scenario_id\"]}'
print('OK - all scores and verdicts match across split')
"
```

**Commit:** `refactor: split commands/scan.py into orchestration, output, and display modules`

---

## Tier 4 — Split driver.py (Session 4)

Target: half day.

**Current state:** `engine/driver.py` is 580 lines with a 340-line `run_scenario()` function mixing turn execution, counter-refusal injection, scoring, and hold-variant logic.

**Target structure:**
- `engine/driver.py` — `run_scenario()` orchestrator (target: under 80 lines)
- `engine/turn.py` — `_execute_turn()` handles a single conversation turn
- `engine/counter_refusal.py` — `_handle_counter_refusal()` isolates the CR injection logic
- `engine/types.py` — `ScenarioResult`, `TurnRecord`, `APICallTiming` dataclasses

**Requirements:**
- `run_scenario()` becomes a coordinator calling into helpers
- `_execute_turn()` takes `(messages, adapter, judge, baseline, turn_config)` and returns a `TurnResult`
- `_handle_counter_refusal()` takes `(response, scenario_context, tracker)` and returns either `None` or injected messages
- Preserve ALL current behavior: hold_variants, counter-refusals, retries, scoring blend
- Same before/after verification as scan.py refactor

**Commit:** `refactor: split engine/driver.py scenario execution into turn and counter-refusal modules`

---

## Tier 5 — Dead Code Removal (Session 5)

Target: 30 minutes.

For each candidate below, verify it's truly unused:
```powershell
grep -r "function_name" src/ tests/ 2>$null
```

**Delete if truly unused:**
- `counter_refusals.py::get_noise_template`
- `counter_refusals.py::get_noise_domains`
- `counter_refusals.py::get_categories`
- `commands/_shared.py::get_scenarios_dir` (superseded)
- `adaptive/cross_family.py::get_provider`

**Rename with `_` prefix (internal plumbing):**
- `personas/loader.py::list_persona_profiles` → `_list_persona_profiles`
- `scenarios/loader.py::validate_scenario` → `_validate_scenario`
- `scenarios/loader.py::load_scenario_from_dict` → `_load_scenario_from_dict`
- `scenarios/loader.py::load_scenario_file` → `_load_scenario_file`
- `scenarios/loader.py::load_scenario_directory` → `_load_scenario_directory`

**Leave public:** `ScenarioValidationError` (exceptions commonly imported by callers).

**Commit:** `chore: remove unused functions and mark internal plumbing with _ prefix`

---

## Tier 6 — Long CLI Commands (Session 6)

Target: 1-2 hours.

Apply the same pattern as scan.py to the remaining monolithic command functions. These are smaller, so helpers can stay in the same file (no new modules needed). Target each main command function under 80 lines.

- `commands/adaptive.py::adaptive()` (226 lines → under 80)
- `commands/memory_delta.py::memory_delta()` (198 lines → under 80)
- `commands/calibrate.py::calibrate()` (188 lines → under 80)
- `commands/rapport_delta.py::rapport_delta()` (171 lines → under 80)

**One commit per command.**

**Commits:**
- `refactor: extract setup/execution/output phases in adaptive command`
- `refactor: extract setup/execution/output phases in memory_delta command`
- `refactor: extract setup/execution/output phases in calibrate command`
- `refactor: extract setup/execution/output phases in rapport_delta command`

---

## Tier 7 — Performance: Conditional Layer 2 (Session 7)

Target: half day.

**Why now:** Clean code from Tiers 3-6 makes this safe to add without tangling.

**Feature:** Skip Layer 2 (LLM judge) scoring on turns where Layer 1 (deterministic) drift is below a threshold. Rapport and early-context turns rarely drift, so judge calls there waste 2-5s per turn with no information gain.

**Design:**
- New flag: `--layer2-threshold FLOAT` (default 0.1 — always run Layer 2; set higher to skip low-drift turns)
- Also add `--mode` flag with presets:
  - `--mode quick`: `--no-counter-refusals --layer2-threshold 0.3`
  - `--mode standard`: `--layer2-threshold 0.15`
  - `--mode deep`: full behavior (current default)
- Document in README that `--mode deep` is the canonical setting for benchmarks; `--mode quick` is for smoke tests

**Commit:** `feat: add --mode and --layer2-threshold flags for speed/signal tradeoff`

---

## Tier 8 — Parallel + Async (Session 8)

Target: half day.

**Feature:** `--parallel N` flag with central token-bucket rate limiter.

**Design:**
- Add `rate_limiter.py` with `TokenBucket` class enforcing RPM per provider
- Add `--parallel N` flag (default 1 = current sequential behavior, max 10)
- Add `--rpm-limit N` flag (default 50 per provider, provider-aware defaults below)
- When `--parallel > 1`, use `asyncio.Semaphore` to limit concurrent scenarios
- All concurrent scenarios share one rate limiter instance
- Results still accumulate and serialize in scenario order
- Partial save still works per-scenario as it completes

**Provider defaults:**
- `openai`: 50 RPM
- `together_ai`: 50 RPM
- `vertex_ai`: 50 RPM
- `bedrock`: 200 RPM (higher pay-per-use limits)

**DO NOT convert the entire codebase to async.** Use `asyncio.run()` as the entry point from the Click command and keep the rest synchronous. Only the scenario execution loop needs to be async.

**Also add async judge scoring:** Fire judge calls and continue to next turn. Collect scores at scenario end. Combined with `--parallel`, this gets full scans to ~15 minutes.

**Commits:**
- `feat: add rate_limiter module for central RPM throttling`
- `feat: add --parallel flag for concurrent scenario execution`
- `perf: async judge scoring (fire-and-forget with scenario-end collection)`

---

## Tier 9 — Zero-Credential Demo (Session 9)

Target: half day. **Growth feature — high priority for launch.**

**Feature:** `voigt-kampff demo --model openai/gpt-4o-mini` runs a curated subset of scenarios with a reasonable free-tier-friendly judge, suitable for first-time users.

**Design:**
- New command: `commands/demo.py`
- Bundled scenario subset: 3-5 scenarios covering financial, medical, security (1 each) with cold pairs for rapport delta
- Uses a free or low-cost default judge (e.g., `openai/gpt-4o-mini` as judge too)
- Prints a 60-second demo header explaining what's happening
- On completion, prints the drift table and a one-liner: "Want to run your own scenarios? See voigt-kampff scan --help"

**Also create:**
- README section: "Try it in 60 seconds" with a one-liner install + demo command
- 60-second gif showing the demo running with live drift (separate from code change)

**Commit:** `feat: add zero-credential demo command for 60-second first-run experience`

---

## Post-Launch Backlog

Not required for launch, but worth tracking:

- `--seed` flag for reproducibility (needed for paper, not demo)
- Streaming responses (diminishing returns after async + parallel land)
- `voigt-kampff validate --diversity` (scenario batch structural check)
- `--all` / `--collection` filter validation fix
- Split `scoring/layer1.py` signal functions into `scoring/signals.py`
- Split `reporting/html_report.py` into `reporting/sections.py` + external CSS file
- Reconcile `layer1.py` vs `_experimental_signals.py` — decide if experimental should be promoted or removed

---

## Session Checklist Template

Copy this for every session:

```
□ Read AGENTS.md (this file)
□ Read code-audit skill
□ Identify which Tier this session corresponds to
□ Read the relevant section of AGENTS.md completely
□ Execute changes in the order listed
□ Verify each change with the commands specified
□ Run code-audit skill on changed files before committing
□ One commit per logical change (not one commit for the whole session)
□ Push to origin/main at end of session
□ Update AGENTS.md if the session revealed new issues or changed the plan
```

---

## Emergency Brakes

Stop and ask the user if:

- A refactor changes scenario scores in before/after diff (should never happen)
- Any test breaks that wasn't broken before
- An import cycle becomes unguarded
- Code-audit flags a regression in any dimension
- The session's scope is unclear or seems to overlap with another Tier
- You encounter a constant, function, or file that wasn't mapped in the audit

Do not improvise. This document is the plan. Deviations need explicit user approval.
