# AGENTS.md — sapien-score Launch Plan

> Active working plan for Claude Code on sapien-score (voigt-kampff CLI).
> Read this file at the start of every session.
> Last rewritten: April 16, 2026. Full replacement of the previous tier-based plan.

---

## North Star

**Ship a public viral launch within 2 weeks.**

The launch is a zero-credential demo command (`voigt-kampff demo`) with a 60-second README gif showing live behavioral drift detection. Everything in this document exists to serve that launch.

If a task does not serve the launch, it is out of scope. It goes to the Post-Launch Backlog at the bottom of this file, or it doesn't get tracked here at all.

---

## Why the Plan Changed

The previous AGENTS.md was tier-ordered for code cleanliness. Tiers 3 through 8 were scheduled before the viral demo (Tier 9). That order optimized for "code Callen is not embarrassed by" over "shipping the launch-critical feature."

The new plan inverts this:

1. **Credibility-load code only.** The files reviewers will actually open (scan.py, driver.py) get refactored. Everything else stays as-is until post-launch.
2. **Launch-critical features first.** The demo command, the README, the performance work that keeps the demo fast.
3. **No scope creep.** Tier 5 (dead code), Tier 6 (long CLI commands), Tier 8 (full parallel/async) are deferred.

---

## Working Principles

Every session follows these rules:

1. **Read the code-audit skill before starting.** Every session. Same rule as the old plan.
2. **One commit per logical change.** No combined commits.
3. **Verify before committing.** Each change has a verification command. It must pass.
4. **Code-audit on changed files before committing.** 7-dimension framework. Any regression stops the commit.
5. **Before/after diff for any refactor.** Run a scan before and after. Health scores and verdicts must be byte-identical. Timing fields and timestamps excluded.
6. **Do not expand scope.** A task in this document is a task. Adjacent cleanup goes in a note at the end of your session report, not a commit.
7. **If something is off, stop and ask.** Especially anything touching scoring, driver, or retry logic.

---

## The Launch Critical Path

Tasks 0 through 8. Each is sized and has an acceptance criterion. Do them in order.

### Task 0 — Replay Infrastructure (foundational)

**Why:** Without deterministic replay, scores from any LLM-driven scan are unreproducible, and "did this refactor change behavior" is unanswerable. Every subsequent task depends on this.

**Scope:** Three sub-tasks, each its own PR.

- Task 0.1 — Trace recording. Every target and judge LLM call gets logged to a structured JSONL trace file alongside scan results. Crash-safe (append-only), handles concurrent scans (unique run_ids), schema-versioned.
- Task 0.2 — Replay mode. A `--replay <trace.jsonl>` flag on `voigt-kampff scan`. Adapter consults the trace first; identical prompt+params returns the recorded response; mismatch fails loudly. Same scan + same trace = byte-identical scores.
- Task 0.3 — Verify command. `voigt-kampff verify <results.json> <trace.jsonl>` re-runs in replay mode, diffs scores, exits 0 on match, non-zero with clear report on mismatch. Usable in CI.

**Hard constraints (all three sub-tasks):**
- No behavior change in default (non-replay) mode. Existing scans continue to work identically.
- Tracing is ON by default. Users opt out with `--no-trace`.
- Traces live in a `traces/` subdirectory relative to the output file's directory.
- Trace filename: `<results_basename>.trace.jsonl` (e.g. `results.json` -> `traces/results.trace.jsonl`).
- All file writes use atomic temp-file + rename pattern to survive crashes mid-scan.
- JSONL format: one JSON object per line, append-only, no rewrites.
- Every trace entry has: `schema_version`, `run_id` (UUID4), `step_id` (monotonic int), `timestamp` (ISO 8601 UTC), `kind` ("target_call" | "judge_call"), `model`, `provider`, `request` (full messages, params, tools), `response` (full content, usage, finish_reason), `duration_ms`, `metadata` (free dict).
- Schema version starts at 1. Any change bumps version. Replay refuses to load unknown versions with a clear error.
- Paths with spaces, unicode, or on network drives must work. Test with a path containing a space and a unicode char before declaring done.
- Replay mismatch errors include: which step, what differed (prompt hash, params hash), and the full recorded vs. current values.

**Branch:** `feat/trace-recording` (0.1), `feat/replay-mode` (0.2), `feat/verify-command` (0.3)

**Size:** 1-2 days total across all three sub-tasks

### Task 1 — Refactor scan.py (credibility)

**Why:** scan.py is the entry point. Any reviewer who evaluates SAPIEN's methodology opens this file first. A 497-line function mixing concerns signals "vibe coded" and undermines the framework's rigor pitch.

**What:** Split `commands/scan.py` into four files:

- `commands/scan.py` — thin Click command, under 60 lines
- `commands/scan_orchestration.py` — `_setup_engine()`, `_run_scenarios()`, argument resolution
- `commands/scan_output.py` — `_build_output_payload()`, serialization, `_timing` handling
- `commands/scan_display.py` — `_render_results()`, cost estimate display, progress callbacks

**Hard constraints:**
- `scan()` must drop to under 60 lines.
- No public API changes. External callers still import from `commands.scan`.
- Preserve the lazy import pattern. Heavy imports (litellm, pandas) stay inside function bodies.
- No behavior changes. The invariant check catches this.

**Baseline:** `baseline_pre_tier3.json` exists at repo root, 8 financial scenarios on `bedrock/deepseek.v3.2` with `openai/gpt-5.4` judge. Copy to `/tmp/before_split.json` before starting.

**Verification:**
```powershell
voigt-kampff scan --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/after_split.json

python -c "
import json
b = json.load(open('/tmp/before_split.json'))
a = json.load(open('/tmp/after_split.json'))
for rb, ra in zip(b['results'], a['results']):
    assert rb['scenario_id'] == ra['scenario_id']
    assert rb['health_score'] == ra['health_score'], f'Score changed for {rb[\"scenario_id\"]}'
    assert rb['verdict'] == ra['verdict'], f'Verdict changed for {rb[\"scenario_id\"]}'
print('OK - scores and verdicts match')
"
```

**Commit:** `refactor: split commands/scan.py into orchestration, output, and display modules`

**Branch:** `refactor/split-scan`

**Size:** half day

### Task 2 — Refactor driver.py (credibility + Task 4 prerequisite)

**Why:** Same credibility argument as scan.py. Also: Task 4 (conditional Layer 2) requires modifying `run_scenario()`, which is easier in a 60-line function than a 340-line one.

**What:** Split `engine/driver.py` into three files:

- `engine/driver.py` — `run_scenario()` orchestrator, under 80 lines
- `engine/turn.py` — `_execute_turn()` handles a single conversation turn
- `engine/counter_refusal.py` — `_handle_counter_refusal()` isolates CR injection logic
- `engine/types.py` — `ScenarioResult`, `TurnRecord`, `APICallTiming` dataclasses

**Hard constraints:**
- `run_scenario()` under 80 lines.
- Preserve all current behavior: hold_variants, counter-refusals, retries, scoring blend.
- Same before/after verification as Task 1.

**Verification:** Same invariant command as Task 1. Run before starting, compare after.

**Commit:** `refactor: split engine/driver.py scenario execution into turn and counter-refusal modules`

**Branch:** `refactor/split-driver`

**Size:** half day

### Task 3 — Build the demo command

**Why:** This is the launch feature. Everything else supports this.

**What:** New command `voigt-kampff demo` that runs a curated scenario subset with no credentials required beyond the target model API key.

**Design:**

- New file: `commands/demo.py`
- Bundled scenario subset: 4 scenarios (3 + cold pair), one each from financial, medical, security, plus the cold variant for rapport delta.
- Default target: user specifies via `--model openai/gpt-4o-mini` (or similar). No default — force explicit choice so they know what they're running against.
- Default scoring: Layer 1 only (deterministic, no LLM judge calls). `--full` flag enables Layer 2 judge scoring for users who want complete scoring at the cost of longer runtime.
- Print a header explaining what's about to happen. Estimated time: under 3 minutes.
- On completion: print the drift table, print the rapport delta from the cold pair, print a one-liner pointing to `voigt-kampff scan --help` for real runs.

**Bundled scenarios:**

- `sapien.financial.crypto_tax_evasion.v1` — drifts reliably on weaker models, clear visual signal
- `sapien.medical.pediatric_dosing_pressure.v1` — medical drift is intuitive to non-technical viewers, 5 escalations keeps it fast
- `sapien.security.password_sharing.v1` — security domain, universally understood by technical audiences
- `sapien.security.password_sharing_cold.v1` — cold variant for rapport delta display

**Hard constraints:**
- No credentials beyond a single provider API key.
- Runs to completion in under 3 minutes on a reasonable connection (Layer 1 only; `--full` may take longer).
- Output fits on one screen. Do not page or require scrolling.
- Exit code 0 on success even if drift is detected. Exit code 1 only on infrastructure failures (API down, missing key, etc.).

**Verification:**
```powershell
# Smoke test with a real key
voigt-kampff demo --model openai/gpt-4o-mini
# Should complete in under 3 min, print 3+1 scenario results + rapport delta
```

**Commit:** `feat: add voigt-kampff demo command for zero-credential first-run experience`

**Branch:** `feat/demo-command`

**Size:** half day

### Task 4 — Conditional Layer 2 judge scoring

**Why:** Demo must feel fast. Current ~30s per turn is too slow for "60-second" framing. Conditional Layer 2 skips the LLM judge on turns where deterministic drift is already near zero. Gets demo closer to the 90-second ceiling comfortably.

**What:**

- New flag: `--layer2-threshold FLOAT` (default 0.1 — always run; higher skips low-drift turns)
- New flag: `--mode` with presets:
  - `--mode quick`: sets `--no-counter-refusals --layer2-threshold 0.3`
  - `--mode standard`: sets `--layer2-threshold 0.15`
  - `--mode deep`: full behavior, current default
- Demo command uses `--mode quick` internally.
- README documents that `--mode deep` is the canonical benchmark setting. `--mode quick` is for demos and smoke tests.

**Hard constraints:**
- Default behavior unchanged. All existing scans behave identically unless user opts into a mode.
- The invariant check from Task 1 still passes on `--mode deep`.

**Verification:**
```powershell
# Demo mode is fast
Measure-Command { voigt-kampff scan --mode quick --model openai/gpt-4o-mini --judge openai/gpt-4o-mini --domains financial --scenarios crypto_tax_evasion }
# Should be well under 60s for one scenario

# Deep mode is unchanged
voigt-kampff scan --mode deep --model bedrock/deepseek.v3.2 --judge openai/gpt-5.4 --domains financial --output /tmp/deep_check.json
# Diff against baseline_pre_tier3.json — must match
```

**Commit:** `feat: add --mode and --layer2-threshold flags for speed/signal tradeoff`

**Branch:** `feat/mode-flag`

**Size:** half day

### Task 5 — README rewrite around the demo

**Why:** The README is what people read after they click the GitHub link. It either converts them to try the demo or loses them.

**What:** Rewrite `README.md` with the demo as the hook.

**Structure:**

1. One-line pitch at the top. Plain English. Example: "Measure how AI models drift under conversational pressure. One command, 60 seconds."
2. "Try it in 60 seconds" section immediately after the pitch. Install + demo command, copy-paste ready. Include a placeholder for the gif.
3. What SAPIEN measures — 2 short paragraphs, no jargon.
4. The key finding that motivates the project — judge sycophancy, or the cross-country result (Kimi beats GPT-4o), whichever lands harder. One chart or table.
5. Full `voigt-kampff scan` usage for serious users.
6. How to cite, how to contribute, license (Apache-2.0).

**Hard constraints:**
- Reading level: technical but not academic. A senior engineer reading it on their phone should get the point in 30 seconds.
- No marketing language ("revolutionary", "groundbreaking", etc.). Callen's credibility comes from precision, not hype.
- No broken promises. Don't claim features that aren't in the launched build.

**Verification:** Callen reads it and approves. There is no automated test for good README writing.

**Commit:** `docs: rewrite README around demo command and viral launch`

**Branch:** `docs/readme-launch`

**Size:** half day

### Task 6 — Record the 60-second gif

**Why:** The README's "Try it in 60 seconds" section needs a gif. Without it, the claim is abstract. With it, it's proof.

**What:** Record the demo running end-to-end. Show the install command, the single `voigt-kampff demo` invocation, and the drift table appearing.

**Hard constraints:**
- Gif under 10 MB (GitHub renders inline up to this size cleanly).
- Total runtime 60 seconds or less. If the demo takes longer, cut dead time between scenarios rather than speeding up the playback — viewers should see realistic behavior.
- No audio needed; this is a silent gif.
- Legible on mobile. Font size 16pt or larger in the terminal.

**Tools:** `gifski` + `asciinema` or `terminalizer`. Callen's call on which.

**Verification:** The gif renders correctly on GitHub's README view (check on desktop + mobile). It shows what the README claims.

**Commit:** Gif lives in `docs/assets/` and is referenced from README. Commit message: `docs: add 60-second demo gif`.

**Size:** half day

### Task 7 — Pre-launch dry run

**Why:** Every launch that didn't do this regrets it. Catches things like "the demo fails on Windows," "the install docs assume Python 3.11 but the CI uses 3.10," "the gif link is broken."

**What:** Send the repo link to 2-3 trusted reviewers before public launch. Get them to run `voigt-kampff demo` on their own machine, fresh clone, no shortcuts.

**Candidates:** Karl Fulljames, Ashley Cooper, Arlin Sorensen, or someone from the MSP ecosystem who hasn't seen the code before.

**Hard constraints:**
- Fresh install on their machine. Not "does it work on Roy Batty."
- Capture their questions verbatim. The first question someone asks is usually the thing the README should have answered.
- Fix issues found, don't rationalize them.

**Verification:** At least 2 reviewers successfully ran the demo without help. Their feedback has been incorporated or explicitly deferred with reasoning.

**Size:** 1-2 days elapsed, though your active time is probably 2-3 hours.

### Task 8 — Launch

**Why:** This is the point.

**What:**

- Public repo visibility flipped on.
- Hacker News post (Show HN format).
- Twitter/X thread.
- LinkedIn post (your MSP audience).
- Email to GTIA data council contacts.
- Coordinate timing with Jay (Synthreo marketing).

**Hard constraints:**
- Launch on a Tuesday or Wednesday morning US time. Not Friday. Not weekends.
- Monitor the first 4 hours actively. Be ready to fix issues fast.

**Size:** half day of active work, plus ongoing monitoring.

---

## Timeline

Rough calendar, assuming a 2-week window starting today (April 16, 2026):

| Days | Work |
|---|---|
| Day 1-2 | Task 0 (replay infrastructure: trace, replay, verify) |
| Day 3 | Task 1 (scan.py split) |
| Day 2 | Task 2 (driver.py split) |
| Day 3 | Task 3 (demo command) |
| Day 4 | Task 4 (mode flag + conditional Layer 2) |
| Day 5 | Task 5 (README rewrite) |
| Day 6 | Task 6 (gif) + start Task 7 (dry run outreach) |
| Days 7-10 | Dry run feedback cycle, scenario rewrites if needed, buffer |
| Day 11 | Final polish, launch assets |
| Day 12 | Task 8 (launch) |
| Days 13-14 | Post-launch monitoring, fast fixes |

Days 7-10 are deliberate buffer. Do not fill them with Tier 5 or Tier 6 work. Use them for scenario rewrites (9 pending) or paper draft progress if the critical path is clear.

---

## Out of Scope for Launch

These are tracked only so they don't get forgotten. Do not touch them before launch.

- Tier 5 dead code removal (purely cosmetic, nobody will notice)
- Tier 6 long CLI command refactors (adaptive, memory_delta, calibrate, rapport_delta work fine, just ugly)
- Tier 8 full parallel + async (Task 4's conditional Layer 2 is enough for demo speed; full async can wait)
- N=30 scans on Haiku, Kimi, Qwen (Arnie track — runs in parallel as overnight work, not blocking launch)
- GPT-5.4 and Gemini 3.1 Pro flagship scans (same)
- Judge sycophancy paper writeup (research track, not launch track)
- Driftproof educational annotations (site task, separate repo)
- `--seed` flag for reproducibility (partially addressed by Task 0 replay; full seed support deferred)
- Streaming responses
- `voigt-kampff validate --diversity`
- Splitting `scoring/layer1.py` or `reporting/html_report.py`

---

## Emergency Brakes

Stop and ask Callen if:

- A refactor changes scenario scores in the before/after diff. Should never happen.
- Any test breaks that wasn't broken before.
- An import cycle becomes unguarded.
- Code-audit flags a regression in any dimension.
- The demo command takes longer than 90 seconds on `openai/gpt-4o-mini`.
- A reviewer in the dry run can't run the demo and the reason isn't obvious.
- You encounter a constant, function, or file that wasn't mapped in the audit.
- The invariant check fails anywhere.

Do not improvise. This document is the plan. Deviations need explicit approval.

---

## Session Checklist

Copy this for every session:

```
[ ] Read AGENTS.md (this file)
[ ] Read code-audit skill
[ ] Identify which Task this session corresponds to
[ ] Read the Task section completely
[ ] Execute changes in the order listed
[ ] Verify with the commands specified
[ ] Run code-audit on changed files before committing
[ ] One commit per logical change
[ ] Push to origin (sapien-score has no synthreo remote)
[ ] Update AGENTS.md if the session revealed a new blocker or scope change
```

---

## Post-Launch Backlog

Things worth doing after the launch lands, tracked here so they're not forgotten:

- Tier 5 dead code removal
- Tier 6 long CLI command refactors
- Tier 8 full parallel + async (`--parallel N` with rate limiter)
- Async judge scoring
- `--seed` flag for reproducibility
- Streaming responses
- `voigt-kampff validate --diversity`
- `--all` / `--collection` filter validation fix
- Split `scoring/layer1.py` signal functions into `scoring/signals.py`
- Split `reporting/html_report.py` into sections + external CSS
- Reconcile `layer1.py` vs `_experimental_signals.py`
- Judge sycophancy paper writeup
- Rapport Delta methodology paper
- Add `judge_model` to scan output top-level metadata (self-documenting baselines)
- N=30 scans on Haiku, Kimi, Qwen for press-ready data
- GPT-5.4 and Gemini 3.1 Pro flagship scans
- 9 pending scenario escalation rewrites
- Adversarial/threat-actor scenario additions
- Hash-commit published benchmark traces (`<trace>.sha256` in git, same pattern as `baseline_pre_tier3.sha256`) — cheap tamper-evidence for launch-quality reproducibility
- Add `run_id` to scan output top-level metadata — lets `verify` detect paired-file mismatch (right model but wrong run)
- Optional: cryptographic signatures on published traces (real tamper-evidence, overkill for launch but worth considering for paper-grade reproducibility)
