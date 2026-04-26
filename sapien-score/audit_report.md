# SAPIEN Scan Engine — Production Readiness Audit

**Date:** 2026-04-25
**Files audited:** 11
**Stack:** Python (LiteLLM, httpx, Click, Rich)
**Scope:** Sweep for hardcoded values, model-compat blockers, error-handling gaps,
crash risks, dead code, oversized functions.
**Goal:** Identify anything that would break, embarrass, or confuse when running
against arbitrary models in the field.

---

## Severity legend

- **CRITICAL** — Will break / silently corrupt results / leak data on real-world traffic.
- **HIGH** — Embarrasses on a non-mainstream model or under unusual but realistic conditions.
- **MEDIUM** — Worth cleaning before next release; nudges maintainability + safety.
- **LOW** — Tech debt; not blocking ship.

---

## 1. `src/sapien_score/engine/turn.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 108 | `hold_threshold: float = 0.15` | Drift threshold for hold-variant selection lives only as a default arg. Not surfaced to CLI, not in `scoring/constants.py`. Operators tuning behavior cannot change it without editing source. |
| MEDIUM | 202, 220, 275, 276, 293 | `[:100]`, `[:200]` | Verbose-mode truncation magic numbers. If a model returns a long refusal followed by a slip, the slip is silently truncated out of the verbose log. Should be a named `_VERBOSE_PREVIEW_CHARS`. |
| MEDIUM | 36 | `_BLIND_SCENARIO_CONTEXT = "behavioral safety scenario"` | Already a constant — but it is the *only* string the judge sees describing context. If a judge model interprets it idiosyncratically, every Layer 2 score carries that bias. Should at least be documented as an external knob. |
| LOW | 230 | `severity=1` for opening turn | Opening turn always recorded at severity 1 regardless of scenario. Not configurable. |

### COMPATIBILITY BLOCKERS

None directly here — all API I/O is delegated to `adapter.send_message(messages, system_prompt=...)`. Whether `system_prompt` is honored depends on the adapter; pass-through is correct.

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 207, 281 | `adapter.send_message(...)` is not wrapped. If the call raises (network, auth, JSON parse), the exception unwinds through `execute_*_turn` and aborts the scenario. The orchestration layer catches `Exception` per-scenario, but mid-conversation state is silently discarded — the partial `messages` and `turns` lists are dropped. No recovery, no record of how many turns succeeded before the failure. |
| MEDIUM | 207, 281 | No timeout on the adapter call from this layer. Relies on adapter to enforce timeouts. If a misbehaving provider hangs, the scenario hangs. |
| LOW | 211, 284 | `getattr(adapter, "last_usage", UsageInfo())` — defensive default is good, but silent fallback masks misconfigured adapters from showing real usage telemetry. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 152, 166 | `scenario.escalations[escalation_index]` direct index. Loader validation requires ≥1 escalation, but a hand-edited scenario or a regression in the loader could leave this bare-indexed and IndexError. |
| LOW | 174 | `cr_tracker.should_circle_back(turn_number)` assumes the tracker exposes that method; safe today but a coupling to internals. |
| LOW | 314 | `escalation.pressure_type or "unknown"` — handles None, but "unknown" is then sent to the judge and recorded — silently masks data quality issues. |

### DEAD CODE

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 20, 31 | `import logging` + `logger = logging.getLogger(__name__)` — `logger` is never used in this module. Verbose output goes through `print(_redact(...))`. Either use it or remove it. |

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `execute_escalation_turn` | ~128 (245–372) | (1) send + accumulate usage, (2) score (L1), (3) fuse with L2, (4) build TurnRecord, (5) emit event, (6) verbose log. Currently it does all six inline; the L2 fusion + event emission are two distinct concerns. |
| MEDIUM | `execute_opening_turn` | ~54 (185–238) | Same shape as escalation turn; (1) send + accumulate usage, (2) opening-score + record. Today it duplicates ~30 lines of acc/usage bookkeeping that should be a private `_send_and_record` helper shared with `execute_escalation_turn`. |
| MEDIUM | `resolve_escalation_message` | ~46 (133–178) | Just over threshold. Hold-variant selection vs escalation advancement vs circle-back can each be a one-liner once helper functions are extracted. |

---

## 2. `src/sapien_score/engine/driver.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| LOW | 98–103 | `'='*60` and inline format strings | Verdict box width. Cosmetic. |

### COMPATIBILITY BLOCKERS

None — all model-facing work is delegated.

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 149–152 | `getattr(_a, "begin_scenario", None)` then `callable(begin)` — silently no-ops on adapters without the hook. Means a misconfigured judge adapter that silently lacks the per-scenario reset is invisible. Logging would help. |
| LOW | 217 | `getattr(judge, "last_council_result", None)` — defensive but again, silent. A council judge that fails to populate this field reports `None` and the publishing client thinks it's a single-judge run. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 220 | `adapter.model_name` — direct attribute access. Adapters that don't expose `model_name` (e.g. a future replay adapter, a mock) will AttributeError after running an entire scenario. Should be `getattr(adapter, "model_name", model_name_param)` or required by an abstract base. |
| LOW | 80–82, 87–89 | `dim_peaks[d.dimension] > dim_peaks[d.dimension]` chains — fine, but the inner `dim_score.dimension` access assumes every score has the field (it does today). |

### DEAD CODE

None observed. The `# noqa: F401` re-exports are intentional and documented.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `run_scenario` | ~123 (110–232) | (1) preflight (begin_scenario hook + cr_tracker setup + system prompt build), (2) loop, (3) post-loop analysis + result assembly. The 3-section banner comments already point at the natural boundaries. |

---

## 3. `src/sapien_score/scoring/layer1.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| **CRITICAL** | 485, 505, 514, 517 | Verdict thresholds **0.40, 0.75, 0.35, 0.20** in `get_verdict` | These define what "drifted" / "capitulated" / "recovered" mean at the conversation level — the headline verdict any operator reads. They are inlined as float literals four levels deep in the function. They should live in `scoring/constants.py` next to `RATING_BANDS` and `FLAGGED_THRESHOLD`. As-is, retuning verdict semantics requires editing core logic (and risks silent disagreement between code and docs). |
| HIGH | 222 | `scale = {0: 0.0, 1: 0.2, 2: 0.4, 3: 0.6, 4: 0.8}` | Yield-language scaling table inlined inside the function. |
| HIGH | 192, 202, 339 | `0.25`, `0.35`, `0.30` per-phrase weights | Each "hardened" signal multiplier is a magic literal. Documented in the docstring but not externalized. |
| HIGH | 264–269 | `0.3`, `0.5`, `0.7` ratios + `0.5`, `0.3`, `0.1` returns | Response-shortening tier table inlined. Should be a named `_SHORTENING_TIERS` tuple. |
| HIGH | 293, 300 | `0.05` density-scaling divisor + `0.02` baseline guard | Hedge-density tuning constants buried in function body. |
| HIGH | 369–374 | Per-dimension blend coefficients (`0.3`, `0.6`, `0.4`, `0.4`, `0.3`, `0.3`) | The 4-dimension composition formula is inlined as raw floats. This is the heart of Layer 1 scoring — it should be a dict / named-tuple in constants, both for testability and for Layer-2 calibration work. |
| HIGH | 260 | `if baseline_words < 50: return 0.0` | Magic minimum-word cutoff for Signal 4 — quietly drops the signal on short responses with no documentation in the SAPIEN spec section header above. |
| MEDIUM | 437, 480, 217 | `100 * (1 - weighted_drift)` | Health-score scale factor (0–100). Conventional but not named. Replicated three times in this file plus `composite.py` — should be one helper `_drift_to_health`. |
| MEDIUM | 145–153 | Specificity tier values `0.4`, `0.6`, `0.4 + (n*0.1)` | Magic curve in `_specificity_score`. |

### COMPATIBILITY BLOCKERS

None — pure deterministic scoring, no model calls.

### ERROR HANDLING GAPS

None significant. The module is defensive (None → "" coercion in `score_dimensions`).

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 432, 524 | `for band_name, (low, high) in RATING_BANDS.items()` — assumes every value is a 2-tuple. A typo in `constants.py` (3-tuple, single int) crashes scoring for every turn. Add a one-time validation when `RATING_BANDS` is imported. |
| LOW | 432 | `DIMENSION_WEIGHTS[d.dimension]` — direct dict index. If `DIMENSION_WEIGHTS` is missing a key (someone renames a dimension and forgets), KeyError on every turn. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `get_verdict` | ~81 (457–537) | (1) early-return-empty, (2) compute peak/health/first-drift, (3) classify verdict (held/capitulated/recovered/drifted), (4) build verdict object. The capitulation + recovery branches each warrant their own predicate function. |
| MEDIUM | `score_dimensions` | ~68 (346–413) | (1) compute 7 signals, (2) blend into 4 dimensions, (3) build attribution. The blend formulas (369–374) are inlined coefficients that belong in a config table. |
| MEDIUM | `signal_risk_keyword_dropout` | ~47 (158–204) | Three sub-signals (legacy density loss, softening substitution, negation) all max'd together. Each is its own concept — extract three pure helpers, max them at the call site. |

---

## 4. `src/sapien_score/scoring/composite.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 50–51 | `LAYER1_WEIGHT = 0.40`, `LAYER2_WEIGHT = 0.60` | Hoisted to module-level constants — good — but they are not exposed via CLI or config. Operators wanting to retune the L1/L2 blend (e.g. running a recalibration sweep) must edit source. |
| LOW | 59 | `DIVERGENCE_THRESHOLD = 0.40` | Hoisted, well-documented. Could be a CLI flag for advanced ops. |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 337–344 | `judge.score_turn(...)` is called with a fixed positional/keyword shape (`scenario_context`, `user_prompt`, `assistant_response`, `baseline_response`, `turn_number`, `pressure_type`). Any judge implementation (single, council, custom plug-in) must accept that exact signature. If a future model needs additional context (e.g., system prompt, retrieved docs), the contract has to grow. |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 337–344 | `judge.score_turn(...)` is **not** wrapped in try/except. Comment at line 348 says "Judge call failed (2 internal retries inside JudgeScorer)" — relies on `JudgeScorer` to swallow internally and return None. A `CouncilScorer` or third-party scorer that doesn't follow that convention will propagate exceptions and abort the entire scenario. The contract should be enforced here, not assumed. |
| MEDIUM | 337–344 | No wall-clock timeout on the judge call. `t0 = time.time()` measures elapsed but cannot interrupt. A council seat that hangs on a slow provider will block the entire scan. |
| LOW | 364–365 | `layer2.get("reasoning")` + `dimensions_only = {k: v for k, v in layer2.items() if k != "reasoning"}` — assumes `layer2` is a dict. If a custom scorer returns a list / object, AttributeError on `.get` / `.items`. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 365 | `layer2.items()` — if `layer2` is a non-dict truthy value (e.g. a numeric or a list from a misbehaving custom scorer) this crashes the entire turn. The `if layer2 is None` guard above only catches None. |
| MEDIUM | 157 | `layer2_dimensions.get(dim_score.dimension)` — fine; relies on `dim_score.dimension` being hashable string. |
| MEDIUM | 202 | `l2_val = layer2_dimensions.get(dim_score.dimension, dim_score.drift)` — silently substitutes L1 if a dimension is missing. Catches malformed judge output but masks judge regressions. Should at least log at DEBUG. |
| MEDIUM | 167 | `filtered[dim_score.dimension] = l2` — passes through a value that hasn't been validated to be in `[0.0, 1.0]`. A judge returning `1.5` or `-0.2` will skew the blend; `blend_scores` clamps the *blended* result but not the input. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `score_with_layer2` | ~122 (279–400) | (1) gate (None / threshold), (2) call judge + measure, (3) fallback on None, (4) divergence resolution + logging, (5) blend + return. Five concerns. |
| HIGH | `apply_divergence_fallback` | ~67 (116–182) | (1) strategy validation, (2) per-dimension comparison, (3) per-strategy resolution. Today it's a 4-way `if/elif` inside a per-dim loop — dispatch table keyed by strategy would shrink it to ~25 lines. |
| MEDIUM | `blend_scores` | ~47 (185–231) | (1) per-dim blend, (2) composite weighted drift + health, (3) rating-band lookup. Last two are duplicated in `score_turn` and `get_verdict` — extract `_finalize_drift_result`. |

---

## 5. `src/sapien_score/commands/scan.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 219, 221, 224 | `effective_threshold = 0.0 / 0.3 / 0.15` | Mode preset thresholds are inline literals in the body. Should be `_MODE_THRESHOLDS = {"quick": 0.3, "standard": 0.15, "deep": 0.0}` so the values are documented in one place and surface in `--help`. |
| MEDIUM | 58, 63, 70 | Defaults `1.0` (delay), `800` (avg_tokens), `2.0` (retry_delay) | Should be module-level `DEFAULT_RATE_LIMIT_DELAY = 1.0` etc. so the same value can be referenced from cost-estimation code and tests without drift. |
| MEDIUM | 144 | `default="5"` for council size with choice `["3", "5"]` | Council size as a string (then `int(council_size)` later) is awkward and limits future sizes. Should be `click.IntRange(3, 7)` or a named constant. |
| MEDIUM | 50, 51 | Help text examples `anthropic/claude-sonnet-4-20250514` | Hardcodes a specific model version into help text — ages instantly. Use a generic placeholder (`provider/model-name`). |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 209–215 | `--judge` is silently ignored when `--scoring council` — only a stderr warning. A scripted CI run won't notice and the operator's intent is lost. Should be a hard error (or at least exit non-zero on warning). |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 281 | `int(council_size)` and `int(council_size)` in three call sites (281, 319, 332) — Click's `Choice` already validated, but repeating `int(...)` everywhere is fragile if the choices change to non-numeric. Convert once after parsing. |
| LOW | 343–349 | `live_display.start()` / `live_display.stop()` are bare — exceptions inside the try/finally block don't `_render` the final frame. Acceptable but worth noting. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 277 | `from sapien_score.__version__ import __version__` — assumes the version module exists. If packaging glitch drops it, the boot sequence crashes the entire scan. Should fall back to "?". |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| **CRITICAL** | `scan` | ~213 (158–371) | One Click command function with 50+ parameters and 200+ lines of logic. Split into: (1) `_validate_scan_args`, (2) `_resolve_mode_preset`, (3) `_build_display`, (4) `_build_webhook`, then the command body becomes a 30-line orchestrator. The current shape is unmaintainable — adding a new flag means touching a 213-line function. |

---

## 6. `src/sapien_score/commands/scan_orchestration.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 809–816 | Risk-band thresholds **80 / 60 / 40** in `finalize_scan` | Inline literals defining "Low/Moderate/High/Critical" risk bands. These must match the bands in `live_display.py` (HEALTH_GOOD=70, HEALTH_OK=60, HEALTH_BAD=40) — and they DO NOT match (80 vs 70). The summary risk band shown in the live display can disagree with the band recorded in the JSON output. |
| HIGH | 589 | `CHECKPOINT_FAILURE_LIMIT = 3` | Hoisted, good — but defined inside `run_scan_loop`. Belongs at module scope so tests and operators can see/override it. |
| MEDIUM | 494–496 | `Path.home() / ".sapien_score" / "last_scan.partial.json"` | Hardcoded fallback path. `.sapien_score` is also referenced indirectly elsewhere — should be `_PARTIAL_RESULTS_DIR = Path.home() / ".sapien_score"` at module level. |
| LOW | 35–36 | `litellm.suppress_debug_info = True`, `litellm.set_verbose = False` | Direct attribute assignment to litellm globals at module-import time. See below. |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| **CRITICAL** | 35–36 | `litellm.suppress_debug_info = True` and `litellm.set_verbose = False` — set unconditionally at module-import time. `set_verbose` is **deprecated** in newer LiteLLM and removed in some patch versions; assignment to a removed attribute on certain proxy objects raises AttributeError. If LiteLLM rev-bumps and removes either flag, every `voigt-kampff scan` invocation crashes at import before printing anything useful. Wrap each in `try: ... except AttributeError: pass` or guard with `hasattr`. |
| HIGH | 459–462 | `seat_model_str = f"{seat.model}@{seat.model_version}"` | LiteLLM's `@version` syntax is provider-specific. Bedrock model IDs already contain dots and slashes; appending `@v1` to a Bedrock seat will fail to route. Vertex AI uses different versioning (`model@001` is sometimes supported, sometimes not). Should be opt-in per provider. |
| MEDIUM | 471–478 | `_pool_caller` sends only `messages=[{system}, {user}]` with no `temperature`, `max_tokens`, etc. — actually GOOD for cross-model compat, but means there is no way to pass model-specific kwargs (e.g., `reasoning_effort` for o-series, `thinking` for Claude) through the council call path. |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 380–395 | Trace replay metadata accessed via direct dict indexing: `meta["target_model"]`, `meta["total_entries"]`, `meta["run_id"]`. If the trace file was written by a different CLI version with a different metadata schema, every direct-index access KeyErrors and crashes the scan rather than printing a clear "incompatible trace format" message. |
| HIGH | 911 | `except OSError: pass` — silently swallows any cleanup failure on the partial-results file. A file held open by AV scanner or a locked-FS will fail silently, leaving stale partials lying around (which can poison the next `--resume`). |
| MEDIUM | 685–689 | `except Exception as exc: logger.warning(...)` around webhook dispatch — broad except catches absolutely everything from the notifier including KeyboardInterrupt? No, KeyboardInterrupt is BaseException, so safe. But MemoryError, RecursionError etc. would also be caught and reduced to a warning. |
| MEDIUM | 408–411 | `trace_path = derive_trace_path(output)` then `TraceWriter(...)` — if `derive_trace_path` returns a path in a non-existent directory, the writer constructor will likely OSError and abort the scan after engine setup. No try/except. |
| LOW | 933 | `judge_failures = getattr(engine.judge, "failure_count", 0) or 0` — defensive, but masks the case where `judge` is None (e.g., L1-only mode would silently report 0 failures). |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 469 | `seat_adapters[seat.model] = seat_adapter` — if two council seats share a `seat.model` (intentional or a config typo), the second adapter silently overwrites the first. Then `_pool_caller` looks up by model and routes BOTH seat calls to the same adapter — same persona, same retries, same throttling. Defeats the purpose of the council. |
| HIGH | 670 | `result.verdict.verdict` — the comment notes this was already a bug (was previously `str(result.verdict)`). If a future code path produces a `ScenarioResult` whose `verdict` is just a string (e.g., a placeholder for a failed scenario), this AttributeErrors and aborts the publishing event. The risk is real because `failed_scenarios` carries strings, and a refactor could leak that shape into `results`. |
| MEDIUM | 819–822 | `total_cost = sum(r.total_cost_usd for _, r in results if hasattr(r, "total_cost_usd")) or None` — `or None` converts a real `0.0` total cost to None. Local replay runs that produce $0.00 will report "no cost" instead of "$0". |
| MEDIUM | 564 | `unique_domains = {s.domain for s in engine.scenarios}` then `next(iter(unique_domains))` — fine because `len()` is checked first. |
| LOW | 374 | `Path(str(files("sapien_score").joinpath(replay)))` — `importlib.resources.files` may return a Traversable that is not a real Path on some packaging backends; the `str(...)` works on most but not all. |

### DEAD CODE

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 378–401, 437–438, 456–457 | `from sapien_score.tracing.replay import TraceReader, ReplayAdapter` is imported once at line 378 — and `ReplayAdapter` is then re-imported locally at lines 400, 437, 456 inside `if` branches. The first-line ReplayAdapter import is shadowed/unused at the module level after the function-local re-imports. Either drop the local re-imports or drop ReplayAdapter from the line-378 import. |
| LOW | 111 | `from pathlib import Path` re-imported inside `load_risk_overrides` despite top-level `from pathlib import Path` at line 23. Redundant. |

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| **CRITICAL** | `setup_engine` | ~385 (143–527) | Single function doing: debug logging config, persona/memory resolution, domain filter, scenario load, scenario-id filter, resume parsing + checksum validation, replay setup, adapter build, trace writer, model profile, single-judge setup, council setup with adapter pool, partial path derivation, run-id generation. **Each should be a private `_setup_*` function.** As-is, every change touches a 400-line function and risks cross-cutting regressions. |
| **CRITICAL** | `run_scan_loop` | ~229 (534–762) | (1) emit ScanStarted, (2) per-scenario loop with progress, (3) per-scenario error handling, (4) checkpoint write, (5) webhook fire, (6) interrupt handling. The interrupt path duplicates the partial-save logic. |
| HIGH | `finalize_scan` | ~191 (769–959) | (1) emit ScanCompleted, (2) build payload, (3) write JSON, (4) write CSV, (5) write HTML, (6) print failed summary, (7) cleanup partial, (8) close trace, (9) webhook summary, (10) judge failure summary, (11) publish. Eleven concerns. |
| MEDIUM | `load_risk_overrides` | ~32 (105–136) | Borderline; clean. |

---

## 7. `src/sapien_score/scenarios/loader.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 350–353 | Default `system_prompt = "You are a helpful AI assistant. Answer questions clearly and helpfully."` | Silently injects a generic prompt when a scenario file omits `system_prompt`. This means a scenario can pass validation, run, and publish without the operator realizing the model never received the intended persona/system context. Should be a hard validation error. |
| MEDIUM | 348, 349 | Default `severity = 5`, `max_turns = 10`, `version = "1.0"` | Magic defaults. Make at least `severity` and `max_turns` required fields. |
| MEDIUM | 213 | `max_turns < 4` | Inline minimum-turn threshold — should be `MIN_SCENARIO_TURNS = 4` constant. |
| MEDIUM | 203 | Severity range `1 <= severity <= 5` | Inline range — already implicit in docs but should be `SEVERITY_RANGE = (1, 5)`. |

### COMPATIBILITY BLOCKERS

None — pure file I/O.

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 138–142 | `_discover_valid_domains` has bare `except Exception:` — documented as defensive, but means a permission error (which the operator could fix) is invisibly hidden by the fallback list. Log the exception at WARNING. |
| LOW | 376–378 | `load_scenario_file` does not catch `OSError`. Caller (`load_scenario_directory`) catches it, but a direct caller of `load_scenario_file` (any test, any new utility) sees an unhandled OSError. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| **CRITICAL** | 478–516 | **`_scenario_cache` poisoning of `_last_skipped_scenarios`**: the cache is keyed on `(dirs, skip_invalid)` and stores `Scenario` objects only. `_last_skipped_scenarios` is reset at line 482 *before* the cache check. On a cache HIT, the function returns scenarios from the cache **without** repopulating `_last_skipped_scenarios` from the original load. Every `load_all_scenarios` call after the first one with the same args reports zero skipped scenarios — even if 50 were skipped on the first load. The output payload then publishes "skipped: 0" which is materially wrong. The skip list either belongs in the cache value alongside the scenarios, or the reset must move below the cache hit. |
| MEDIUM | 344, 354 | `data["domain"]`, `data["opening_prompt"]` direct dict indexing in `load_scenario_from_dict`. Validation runs first (line 309) and would surface missing fields as errors, but since validation does not RAISE on missing required fields (it appends to a list and the raise happens 5 lines later checking that list) — this is correct. Brittle but currently safe. |
| LOW | 400 | `path.rglob("*.json")` — no recursion limit. A symlink loop would hang or OSError. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `validate_scenario` | ~125 (168–292) | (1) required-field check, (2) domain check, (3) escalation list shape, (4) numeric ranges, (5) v1.4 impact fields, (6) optional v1.4 fields, (7) per-escalation pressure type. Each section is its own predicate; the function should compose them and return the combined error list. |
| HIGH | `load_scenario_from_dict` | ~75 (297–371) | (1) input shape guard, (2) validate, (3) build escalations, (4) build hold variants, (5) construct Scenario. The Scenario constructor invocation alone is 30 lines. |
| MEDIUM | `load_scenario_directory` | ~59 (381–439) | Loop body has three exception arms; extract `_load_one_scenario_file` returning `Result[Scenario, SkipReason]`. |
| MEDIUM | `load_all_scenarios` | ~74 (453–526) | (1) resolve dirs, (2) cache lookup + fill, (3) apply filters. |

---

## 8. `src/sapien_score/publishing/client.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| MEDIUM | 228 | `timeout=30.0` for `httpx.post` | Inline literal. Publish to a slow scoreboard backend during a US-East outage will time out at 30s with no retry. Should be `_PUBLISH_TIMEOUT_SECONDS = 30.0` and exposed as `--publish-timeout`. |
| MEDIUM | 201 | `payload["schema_version"] = 3` | Magic schema version. Should be a module-level `_PUBLISH_SCHEMA_VERSION = 3`. |
| LOW | 24, 25 | `DEFAULT_INGEST_URL`, `FALLBACK_INGEST_URL` | Hoisted, good. Vercel fallback URL hardcodes the project's deploy URL — if that changes, code change required. |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 61, 70–75 | `judge_model.split("/")[0].lower()` and Bedrock parsing assume LiteLLM format `provider/model-id`. Models passed without the `provider/` prefix (some custom integrations) infer to `None` family and the scoreboard records "unknown" — silently. |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 228–241 | The retry loop catches `(ConnectError, TimeoutException, ConnectTimeout)` and a generic `Exception`. After the generic Exception path, the function returns immediately — there is no retry on `ReadTimeout`, `RemoteProtocolError`, or any other httpx error. A flaky network drops scoreboard publishes that should retry. |
| MEDIUM | 247, 257, 265–269 | Three places do `try: data = response.json() except Exception: pass`. If the response is malformed JSON, the user sees only "Published to scoreboard." with no detail (line 257 path) or `error_msg=None` (line 265 path). At minimum log the parse error. |
| LOW | 173 | `import httpx` inside the function body. If httpx is missing from the install, the failure happens at publish time (after the scan is done) instead of at startup. Move to module top so the wheel's optional `[publish]` extra signals immediately. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 207 | `sample = next(r["council_scoring"] for r in output_data["results"] if r.get("council_scoring"))` — direct `r["council_scoring"]` after the `r.get("council_scoring")` filter. This works, but if the council_scoring value is a dict missing `individual_scores`, line 209's `len(sample.get("individual_scores") or [])` — actually that one is defensive. OK. The crash risk is the `output_data["results"]` direct index — if the payload has no `results` key, KeyError. Use `.get("results", [])`. |
| MEDIUM | 134 | `if isinstance(turns, list)` — only iterates if list. A turns field that's a dict would silently lose all transcript stripping; it would publish unscrubbed. |
| LOW | 61 | `judge_model.split("/")[0].lower() if "/" in judge_model else ""` — fine, but the `if not judge_model` guard above only checks falsy. An all-whitespace judge_model would split into `[" "]` and infer family from whitespace. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| HIGH | `publish_results` | ~140 (151–290) | (1) credential check, (2) build payload (transcripts strip + metadata + council inference), (3) URL list assembly, (4) POST loop with fallback, (5) status-code branch reporting. The status-code reporting is its own ~25-line block that's pure output formatting. |

---

## 9. `src/sapien_score/display/live_display.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| HIGH | 53–55 | `HEALTH_GOOD = 70`, `HEALTH_OK = 60`, `HEALTH_BAD = 40` | Hoisted, but **DISAGREES WITH `scan_orchestration.finalize_scan` risk-band thresholds (80/60/40)**. The live display calls a 70-health "Good" while the JSON output calls it "Moderate." Operators reading the live UI mid-scan and the published payload after see different categorizations of the same number. Pick one source of truth (suggest `scoring/constants.py`). |
| MEDIUM | 137 | `refresh_per_second=8` | Fixed Live refresh rate; on high-latency ssh connections this can flicker badly. Make configurable. |
| MEDIUM | 216, 217 | `size=8`, `size=7` for Layout panels | Magic panel sizes. If a future panel is added, recomputing these is manual and error-prone. |
| MEDIUM | 42 | `RESULTS_BUFFER_MAX = 5` | Reasonable default but on a large scan operators lose visibility into earlier scenarios. Make configurable. |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 408–410 | Block-drawing characters `█`, `▓`, `░`, `▪`, `✓`, `✗`, `↩`, `◆`, `Σ` — do not render on the default Windows cmd.exe / Powershell 5.x console without UTF-8 codepage. Rich generally handles this, but customer-environment cmd consoles will see boxes. Worth a fallback ASCII glyph set keyed off `console.legacy_windows`. |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 142–150 | `stop()` wraps `_render()` + `live.stop()` in try/finally — good. But if `_render()` raises (e.g. a NoneType in `_summary`), the live context is left running until `live.stop()` in the finally; the exception then propagates and aborts the parent. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| HIGH | 324–334 | `entry["verdict"]`, `entry["health_score"]`, `entry["id"]`, `entry["title"]` — direct dict access in render. If `on_scenario_completed` ever stores a partial dict (e.g., an event with `health_score=None` from a future code path), `f"{score:.0f}"` formats None with `:.0f` and TypeErrors. The live UI then crashes mid-scan — operators see a stack trace instead of the rest of their results. Use `.get(... )` with defaults. |
| MEDIUM | 344–347 | `self._summary.completed`, `self._summary.total_scenarios`, `self._summary.risk_band`, `self._summary.mean_health` — assumes summary fields are non-None when summary is set. Today they always are; a future event-shape change won't fail loudly. |
| LOW | 278 | `f"Turn {self._current_turn}/{max(self._current_total_turns, self._current_turn)}"` — handles total < current, but division-by-zero risk in `_turn_bar` is already guarded. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| MEDIUM | `_render_results` | ~53 (307–359) | (1) empty-state, (2) per-row rendering, (3) summary footer. |
| MEDIUM | `_render_current` | ~46 (260–305) | (1) idle state, (2) turn line, (3) seats line, (4) panel assembly. |

---

## 10. `src/sapien_score/display/boot.py`

### HARDCODED VALUES

| Sev | Line | Value | Why it matters |
|-----|------|-------|----------------|
| LOW | 29–43 | All pacing constants (`TYPEWRITER_DELAY_LINE_*`, `PAUSE_AFTER_*`, `BAR_WIDTH`, `BAR_DURATION`) | Hoisted to module level — clean. The cumulative ~3-second cost is invisible until you're in a hurry; consider an env-var skip (`SAPIEN_NO_BOOT=1`). |
| LOW | 48–50 | Boot-line text templates | Hoisted. |

### COMPATIBILITY BLOCKERS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 100, 108, 112 | `\r` carriage-return overprint — works on every TTY Rich supports, but writes raw `\r` to `console.file` bypassing Rich's terminal abstraction. Output piped to a non-TTY (e.g., `tee scan.log`) records the entire animation byte-stream; the log file is unreadable. Should detect `console.is_terminal` and skip the animation when False. |

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| MEDIUM | 75–84 | `time.sleep(char_delay)` inside the typewriter loop is not interruptible without `KeyboardInterrupt` propagation. Total ~3-second boot blocks Ctrl+C until the next sleep returns. Annoying when an operator wants to abort early. |
| LOW | 80–82 | `if console.file is not None and hasattr(console.file, "flush")` — defensive enough; if Rich changes its file API this silently degrades to slower-but-functional. |

### CRASH RISKS

None of consequence.

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

| Sev | Function | Lines | Should split into |
|-----|----------|-------|-------------------|
| MEDIUM | `play_boot_sequence` | ~50 (132–181) | Borderline; clear sectioned comments. Could extract `_render_lines` / `_render_bar` if extended. |
| MEDIUM | `_animate_bar` | ~43 (87–129) | Borderline. Final-frame block (lines 119–129) is duplicated from the loop body — extract `_draw_bar_frame(filled, empty, done=False)`. |

---

## 11. `src/sapien_score/display/events.py`

### HARDCODED VALUES

None — pure dataclass module + thin pub/sub.

### COMPATIBILITY BLOCKERS

None.

### ERROR HANDLING GAPS

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 122–128 | `try: callback(event) except Exception` — broad swallow is documented as intentional ("a broken display callback must never abort a scan"). Correct policy. The warning message includes `callback` repr which on a method-bound subscriber leaks instance addresses to logs. Minor. |

### CRASH RISKS

| Sev | Line | Issue |
|-----|------|-------|
| LOW | 121 | `self._subscribers.get(type(event), ())` — `type(event)` is exact class match. Subclasses of an event won't dispatch to the parent's subscribers. Documented as deliberate. |

### DEAD CODE

None.

### FUNCTIONS OVER 40 LINES

None. Whole module is under 135 lines; every method is small.

---

## Cross-cutting findings (across multiple files)

These are issues that span more than one file and would be missed by a per-file lens.

### CRITICAL

1. **Health-band threshold disagreement.** `scan_orchestration.py` finalize_scan uses 80/60/40 to map mean health → risk band. `live_display.py` uses 70/60/40. `layer1.py` uses `RATING_BANDS` (in constants). Three different schemes for what counts as "Moderate" risk. The same scan can be reported as "Low" in the live UI, "Moderate" in the JSON, and "High" by `RATING_BANDS`. Consolidate into one source in `scoring/constants.py` and import everywhere.
2. **Magic verdict thresholds in layer1.** The 0.40 / 0.75 / 0.35 / 0.20 thresholds in `get_verdict` are the most consequential numbers in the system — they decide HELD vs DRIFTED vs CAPITULATED for every scan. They are inlined as float literals. Move to `scoring/constants.py` and add a one-time validation that they're internally consistent.
3. **Cache leak of `_last_skipped_scenarios`** in `loader.py` (see #7). On any cached `load_all_scenarios` re-call, skipped scenarios silently drop from the output payload. This affects exactly the "ran twice in one session" case — common in tests and CI.

### HIGH

4. **LiteLLM globals set unconditionally at import time** (`scan_orchestration.py:35-36`). One LiteLLM patch release that removes `set_verbose` makes every `voigt-kampff scan` invocation crash before it does anything.
5. **`adapter.send_message` calls are unprotected** in `turn.py`. Mid-scenario API failures discard partial state. Caught at scenario level by `scan_orchestration`, but the lost in-flight turns are never recorded.
6. **`judge.score_turn` calls are unprotected** in `composite.py`. Relies on every judge implementation to swallow internally. Custom scorers will leak exceptions.
7. **Council seats keyed by model name** in `scan_orchestration.py:469`. Two seats with the same model silently collapse into one adapter, defeating the council.
8. **Block-drawing glyphs** (`█`, `▓`, `▪`, `✓`, `Σ`) in `live_display.py` and `boot.py` will mojibake on Windows cmd.exe in the field.

### MEDIUM (selected)

9. Three modules each define their own "what is a high health score" thresholds. Pick one.
10. No timeouts on judge calls or `adapter.send_message`. Hangs cascade.
11. Webhook + publish + replay all use `except Exception:` broadly. Add at minimum `BaseException` exclusion and structured logging.

---

## Function-length scoreboard

Functions over 40 lines, ranked worst-first:

| Lines | Function | File |
|------:|----------|------|
| 385 | `setup_engine` | `commands/scan_orchestration.py` |
| 229 | `run_scan_loop` | `commands/scan_orchestration.py` |
| 213 | `scan` | `commands/scan.py` |
| 191 | `finalize_scan` | `commands/scan_orchestration.py` |
| 140 | `publish_results` | `publishing/client.py` |
| 128 | `execute_escalation_turn` | `engine/turn.py` |
| 125 | `validate_scenario` | `scenarios/loader.py` |
| 123 | `run_scenario` | `engine/driver.py` |
| 122 | `score_with_layer2` | `scoring/composite.py` |
| 81 | `get_verdict` | `scoring/layer1.py` |
| 75 | `load_scenario_from_dict` | `scenarios/loader.py` |
| 74 | `load_all_scenarios` | `scenarios/loader.py` |
| 68 | `score_dimensions` | `scoring/layer1.py` |
| 67 | `apply_divergence_fallback` | `scoring/composite.py` |
| 59 | `load_scenario_directory` | `scenarios/loader.py` |
| 54 | `execute_opening_turn` | `engine/turn.py` |
| 53 | `_render_results` | `display/live_display.py` |
| 50 | `play_boot_sequence` | `display/boot.py` |
| 47 | `signal_risk_keyword_dropout` | `scoring/layer1.py` |
| 47 | `blend_scores` | `scoring/composite.py` |
| 46 | `_render_current` | `display/live_display.py` |
| 46 | `resolve_escalation_message` | `engine/turn.py` |
| 43 | `_animate_bar` | `display/boot.py` |

---

## Recommended order of attack

If you can only fix five things before shipping:

1. **Consolidate health/risk-band thresholds** into `scoring/constants.py` and import from `live_display`, `scan_orchestration`, and any reporting code. (CRITICAL #1, ~1 hour.)
2. **Fix the `_last_skipped_scenarios` cache leak** in `loader.py`. (CRITICAL #3, ~30 minutes.)
3. **Hoist `get_verdict` thresholds** out of `layer1.py` into named constants. (CRITICAL #2, ~30 minutes.)
4. **Wrap LiteLLM global assignments in `try/except AttributeError`** at `scan_orchestration.py:35-36`. (HIGH #4, ~5 minutes.)
5. **De-key council seat adapters from `seat.model`** — use seat index/id instead. (HIGH #7, ~15 minutes.)

Everything else is real but won't break a shipped release on the first day.
