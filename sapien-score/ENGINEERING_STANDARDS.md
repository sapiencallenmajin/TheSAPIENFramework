# Synthreo Engineering Standards

> Universal engineering standards for all Synthreo repositories, extracted from the
> Wingtip codebase as the organizational reference implementation. These standards
> apply equally to Python (sapien-score), Flask (Esper/synthreo-pulse), TypeScript
> (Wingtip), and future repos regardless of language or framework.
>
> **This document is the canonical reference.** Every repo's `AGENTS.md` builds on top
> of these rules with repo-specific additions. When `AGENTS.md` and this document
> conflict, this document wins unless the repo explicitly marks the deviation in its
> "When to Deviate" section.
>
> **Every Claude Code session and every code review starts here.**

---

## 1. File and Directory Standards

### Feature-Slice Architecture

Organize code by **business domain**, not by technical layer. A feature owns its UI,
logic, types, and tests together. This keeps related code physically close and makes
cross-feature boundaries explicit in imports.

**Good:**
```
features/
├── chat/
│   ├── components/
│   ├── hooks/
│   ├── types.ts
│   └── tests/
└── settings/
    ├── components/
    ├── hooks/
    └── types.ts
```

**Bad (technical layering):**
```
components/
hooks/
types/
tests/
```

The bad version scatters related code and forces every change to touch four directories.

### Directory Naming

- `kebab-case/` for directory names
- All lowercase, no spaces, no underscores in directory names

### File Naming

| Context | Convention | Example |
|---|---|---|
| React components | kebab-case.tsx | `chat-view.tsx` |
| React hooks | camelCase.ts with `use` prefix | `useChat.ts` |
| Python modules | snake_case.py | `scan_orchestration.py` |
| Python classes | PascalCase in snake_case files | `ScenarioResult` in `types.py` |
| Config files | kebab-case | `benchmark-template.yaml` |
| Documentation | UPPER_SNAKE_CASE for top-level | `README.md`, `AGENTS.md` |

### File Size Limits

**Hard limits** — files exceeding these must be split before the PR merges:

| Type | Soft limit | Hard limit |
|---|---|---|
| Utility modules | 200 lines | 300 lines |
| Feature components | 200 lines | 400 lines |
| Orchestrators/engines | 400 lines | 600 lines |
| Generated code (schemas, protobufs) | No limit | No limit |

**What triggers a split:** Files grow past the soft limit only when they own a single
cohesive domain (auth flow, API client, scoring engine). When a file starts mixing
concerns, extract before the limit is hit.

### Dependency Graph Direction

Dependencies must flow in one direction. Establish a layer order for your repo and
enforce it:

```
types → lib/utils → domain logic → orchestration → entry points
```

**Concrete example (typical repo):**
```
types.ts → lib/ → hooks/domain → features/ → app/routes/
constants.py → utils/ → engine/ → commands/ → CLI entry
```

**Zero unguarded circular imports.** Lazy imports at function level are acceptable
when explicitly commented as circular-dependency workarounds. Module-level cycles are
a merge blocker.

---

## 2. Function and Module Design

### Size Limits

| Category | Target | Maximum |
|---|---|---|
| Pure utility function | 5-20 lines | 40 lines |
| API wrapper | 5-15 lines | 25 lines |
| Focused domain function | 30-80 lines | 100 lines |
| Orchestrator function | 80-200 lines | 300 lines |
| UI component | 20-170 lines | 250 lines |

Functions exceeding the maximum must be decomposed before merging. Orchestrators
should be the only exception and must clearly delegate to sub-functions, not contain
inline logic.

### Single-Responsibility Examples

Every function or module should be describable in one sentence without using the word
"and." If you find yourself saying "this function does X and Y," split it.

**Good (single responsibility):**
- "Routes a form field to the correct component based on dataType"
- "Extracts the error message from an API response"
- "Builds a typed URL from base + path + query params"

**Bad (multiple responsibilities):**
- "Loads messages and manages scroll position and handles input"
- "Fetches data, transforms it, caches it, and renders the UI"

### Orchestrator Pattern for Large Features

When a feature is genuinely large, use a **hub-and-spoke orchestrator** that composes
focused sub-units. The orchestrator owns coordination; the sub-units own execution.

**Example (any language):**
```
orchestrator (200 lines, coordinates)
├── sub_unit_A (50 lines, owns concern A)
├── sub_unit_B (50 lines, owns concern B)
├── sub_unit_C (50 lines, owns concern C)
└── sub_unit_D (50 lines, owns concern D)
```

vs.

```
god_function (800 lines, owns everything) ❌
```

The orchestrator's body should read like pseudocode describing the algorithm. Every
detail is in a sub-unit.

---

## 3. Typing and Validation Standards

### Strict Typing

- **TypeScript:** Zero `any`. Enforced by `@typescript-eslint/no-explicit-any: 'error'`.
  Use `unknown` + narrowing, discriminated unions, or generic types.
- **Python:** Type hints on all function signatures. Use `mypy` or `pyright` in CI.
  Use `Protocol`, `TypedDict`, and `dataclasses` to give structure to parameters.
- **Any language:** Never use an untyped "bag of fields" (`dict[str, Any]`,
  `Record<string, unknown>`) as a public API. Types are documentation.

### Validate at System Boundaries

Validate external inputs (API responses, user input, config files, env vars) at the
boundary. Trust internal code to respect its own type contracts.

**Good:**
- API client normalizes response shape before returning
- CLI parses args into a typed config object at entry
- Scenario loader validates JSON against schema on load

**Bad:**
- Every function that touches an API response re-checks `if 'items' in response:`
- UI components defensively handle `undefined` for fields the API always returns

### Discriminated Results for Expected Failures

For operations that have expected failure modes (auth, validation, parse failures),
return a discriminated result instead of throwing.

**TypeScript:**
```typescript
type Result<T> =
  | { success: true; data: T }
  | { success: false; error: string };
```

**Python:**
```python
@dataclass
class Success(Generic[T]):
    data: T

@dataclass
class Failure:
    error: str

Result = Union[Success[T], Failure]
```

Callers narrow with `if result.success` / `if isinstance(result, Success)` — no
try/catch at every call site.

**Reserve exceptions for truly unexpected failures:** network outages, out-of-memory,
programmer errors. Not for "user entered wrong password."

---

## 4. Error Handling Standards

### Three-Layer Error Pattern

Every error must have clear answers to three questions:

1. **Who sees it?** (user via UI/CLI, developer via logs, or both)
2. **What do they see?** (user-friendly string vs stack trace)
3. **What happens next?** (retry, abort, fallback, ignore)

### Error Surfacing

| Audience | Mechanism | Message style |
|---|---|---|
| End user | UI toast, CLI error line, HTTP response body | Plain language, actionable |
| Developer (dev env) | Console + stack trace | Full detail |
| Developer (production) | Error tracking service (Sentry) | Full detail + context |

**Never:**
- Show stack traces to users
- Log passwords, tokens, PII to any service (including Sentry)
- Use `console.log` for error reporting (use `console.error` or dedicated logger)
- Swallow errors silently (`try { ... } catch {}` with no body)

### Retry Strategy

Retries must be:

1. **Idempotent-safe.** Never retry operations that aren't safe to repeat.
2. **Bounded.** Max 3 attempts total (1 initial + 2 retries) for transient errors.
3. **Backed off.** Exponential or staged backoff (e.g., 2s, 5s, 15s).
4. **Selective.** Distinguish retryable errors (rate limits, 5xx, network) from
   non-retryable (400, 401, 403, 404). Never retry non-retryable errors.
5. **Logged.** Each retry attempt logs the reason.

---

## 5. Testing Requirements

### What Must Be Tested

- **Pure functions with deterministic logic.** Input → output. Test all branches.
- **Parsers, validators, and transformers.** Edge cases: empty, malformed, boundary values.
- **Business logic in isolation.** Scoring algorithms, pricing calculations, state machines.
- **Error handling paths.** Explicitly test the failure modes, not just the happy path.

### What Should NOT Be Tested

- **Framework internals.** Don't test React's rendering, Flask's routing, Click's parsing.
- **Type system.** TypeScript/mypy already enforces shape; tests shouldn't duplicate.
- **Trivial getters/setters.** No value added.
- **Third-party library behavior.** Trust the library or replace it.

### Mock Only at I/O Boundaries

Mock only at the **system boundary**: the network, the filesystem, the OS credential
store, the clock. Do not mock internal modules — run them for real in tests.

**Why:** Mock-heavy tests pass while the real integration breaks. Boundary mocks catch
real bugs.

**Good:** Mock `fetch()`, `fs.readFile()`, `SecureStore.setItem()`, `time.time()`
**Bad:** Mock your own `auth.login()` function in a test of the login screen

### Test Organization

Tests mirror the structure of the source:

```
src/
└── features/
    └── chat/
        └── chat-messages.ts
tests/
└── features/
    └── chat/
        └── chat-messages.test.ts
```

Or colocated (valid alternative):
```
src/
└── features/
    └── chat/
        ├── chat-messages.ts
        └── chat-messages.test.ts
```

Pick one convention per repo. Don't mix.

---

## 6. Documentation Standards

### File Headers

Every source file starts with a brief header explaining scope and constraints:

**TypeScript:**
```typescript
/**
 * Chat message mapper.
 *
 * Converts API response shape to local Message type.
 * No side effects, no React imports, no Sentry.
 * Called from useMessageLoader on initial load and pagination.
 */
```

**Python:**
```python
"""Scan orchestration for voigt-kampff CLI.

Coordinates scenario execution, retry logic, and result assembly.
Does not handle CLI argument parsing (see commands/scan.py) or
output formatting (see commands/scan_display.py).
"""
```

### Comments Explain WHY, Not WHAT

The code shows what it does. Comments justify design decisions, document non-obvious
constraints, and explain tradeoffs.

**Bad:**
```python
# Loop through users and add their email to the list
for user in users:
    emails.append(user.email)
```

**Good:**
```python
# Using set() instead of dedup-after-loop because the user list can
# exceed 10k entries in enterprise deployments and this is on the
# auth hot path.
unique_emails = {user.email for user in users}
```

### README Structure

Every repo's README must have:

1. **One-sentence description** — what this is
2. **Quick start** — minimum commands to run it
3. **Architecture overview** — 2-3 paragraphs on how the pieces fit together
4. **Installation** — full setup for a new developer
5. **Development** — how to run tests, lint, build locally
6. **Deployment** — how to ship (or link to deployment runbook)
7. **License** — license text or pointer to LICENSE file

### User-Facing Strings

All user-visible error messages, labels, button text, and notifications live in a
single constants file per repo. No string literals scattered through components.

**Rationale:**
- Prevents duplicate/inconsistent messaging
- Enables future i18n without archaeology
- Makes tone and voice reviewable in one place

---

## 7. Dependency Management

### External Library Abstraction

Wrap external libraries at a single point of contact. Never import third-party
libraries directly throughout the codebase.

**Examples:**
- Toast library → one `toast.ts` / `toast.py` module with `show_success()` and
  `show_error()` functions
- Error tracking (Sentry) → one `observability.ts` / `observability.py` module
- HTTP client (fetch/requests/httpx) → one `api-client.ts` / `api_client.py` module
- CSS class utilities (clsx, classnames) → one `cn()` helper

**Rationale:** When you need to replace the library (performance, licensing, features),
you change one file instead of 80.

### Design Tokens Centralized

All colors, spacing, typography, radii, etc. live in a single constants module.
Enforced by linter where possible.

**Why:** Design consistency, dark mode support, theming, branding updates all touch
one file.

### Generic API Client Pattern

One generic `apiFetch<T>()` / `api_call[T]()` function handles URL building, auth
headers, error extraction, and response parsing. Every endpoint is a thin 3-5 line
typed wrapper.

**TypeScript:**
```typescript
async function apiFetch<T>(path: string, options?: RequestOptions): Promise<T> {
  // ... shared logic
}

// Every endpoint is a 3-line wrapper:
export const getUser = (id: string) =>
  apiFetch<User>(`/users/${id}`);
```

**Python:**
```python
def api_call(path: str, method: str = "GET", **kwargs) -> dict:
    # ... shared logic
    pass

# Every endpoint is a 3-line wrapper:
def get_user(user_id: str) -> User:
    response = api_call(f"/users/{user_id}")
    return User.from_dict(response)
```

---

## 8. Commit and PR Standards

### Commit Message Format

Use conventional commits. Every commit starts with a type prefix:

- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — structural change, no behavior change
- `perf:` — performance improvement
- `chore:` — tooling, deps, configs, non-functional changes
- `docs:` — documentation only
- `test:` — adding or fixing tests
- `ci:` — CI configuration changes

**Good commit messages:**
```
fix: serialize timing data to JSON output
feat: add --no-counter-refusals flag for faster benchmark runs
refactor: split commands/scan.py into orchestration, output, and display modules
chore: correct license to Apache-2.0 (was incorrectly AGPL)
```

**Bad commit messages:**
```
fix stuff
updates
WIP
working on it
Merge branch 'main' into feature ❌ (avoid merge commits in feature branches)
```

### Commit Scope Discipline

**One logical change per commit.** If a commit message needs "and" in it, split the commit.

- Adding a feature? One commit.
- Refactoring before adding the feature? Separate commit before.
- Fixing a typo you noticed? Separate commit.
- Updating documentation? Separate commit.

### Branch Strategy

- **Main:** Always deployable. Protected.
- **Feature branches:** `feat/description`, `fix/description`, `refactor/description`
- **Long-running refactors:** Work on a branch, land in small PRs against that branch,
  merge branch to main at the end.

### PR Requirements

Every PR must:

1. **Describe the change.** What, why, what's the verification.
2. **Pass CI.** Lint, type check, tests, build.
3. **Stay scoped.** No "while I was in there" changes.
4. **Have a clean commit history.** Squash or rebase before merging. No "fix typo" commits in main.
5. **Preserve behavior on refactors.** For any refactor PR, include evidence that
   behavior is unchanged (test output, before/after logs, screenshots).

---

## 9. Review Checklist

Use this before every commit (human review or Claude Code self-review):

### Code Quality
- [ ] No file exceeds size limits without justification
- [ ] No function exceeds size limits without justification
- [ ] Every file has a header comment explaining scope
- [ ] Every public function has type hints/signatures
- [ ] No `any` / `Any` without comment explaining why
- [ ] No TODO/FIXME/HACK comments added (fix it or file an issue)

### Consistency
- [ ] Naming follows repo conventions
- [ ] File organization follows feature-slice architecture
- [ ] New constants go in the existing constants module, not inline
- [ ] New user-facing strings go in the strings module

### Error Handling
- [ ] Expected failures use Result types, not exceptions
- [ ] User-facing error messages are plain language and actionable
- [ ] Retries are bounded and backed off
- [ ] No silent error swallowing

### Testing
- [ ] New pure functions have unit tests
- [ ] New error paths have tests
- [ ] Mocks are at system boundaries only
- [ ] Tests don't assert framework behavior

### Dependencies
- [ ] No new external library imports outside the abstraction layer
- [ ] Dependency graph is still one-directional (no new circular imports)
- [ ] New runtime dependencies added to package manifest

### Documentation
- [ ] Comments explain WHY, not WHAT
- [ ] Non-obvious design decisions are documented
- [ ] README updated if public API changed
- [ ] CHANGELOG updated if user-facing behavior changed

---

## 10. When to Deviate

These standards are strong defaults, not inflexible rules. Legitimate deviations
exist. The rule is: **deviations must be explicit, documented, and approved.**

### Acceptable Deviations

1. **Generated code exceeds file size limits.** OK. Schemas, protobufs, OpenAPI
   clients, migration files. Add a header comment: `// Generated file - do not edit.`

2. **Orchestrator functions exceed single-responsibility.** OK when clearly marked.
   `useChat` is 358 lines because it coordinates 5 sub-hooks. The orchestrator pattern
   is documented and delegates actual work to focused sub-units.

3. **Framework entry points (routes, CLI commands) are thin adapters.** OK.
   Routes and CLI commands can have minimal logic (parse input, call domain function,
   render output) even if that makes them technically "trivial."

4. **Test code conventions may differ from production code.** OK. Tests can be longer
   per function and more repetitive for clarity. `describe` blocks don't count toward
   function size limits.

5. **Third-party library adaptation code** that must mirror library patterns. OK.
   A module wrapping React Query must sometimes use React Query idioms.

### How to Document a Deviation

Add a comment at the top of the file or function:

```typescript
/**
 * DEVIATION: This file is 580 lines because it owns the entire scoring
 * algorithm and splitting it would fragment related logic. Re-evaluate
 * during the 2026Q3 refactor.
 */
```

```python
"""DEVIATION: This function is 340 lines because it orchestrates the
entire scenario execution loop. All real work is delegated to
_execute_turn() and _handle_counter_refusal(). Re-evaluate if it grows.
"""
```

### How This Document Changes

These standards evolve as the codebase evolves. Propose changes via PR to this
document. Changes require approval from:
- The tech lead / CTO (Ivan for Synthreo)
- One other senior engineer

Changes effective on merge. Existing code gets grandfathered; new code follows new standards.

---

## 11. How This Document Is Used

### In Every Repo

Each repo has a copy of this document at the root: `ENGINEERING_STANDARDS.md`.

Each repo also has `AGENTS.md` which:
1. References this document as the base
2. Adds repo-specific rules (build commands, deployment, quirks)
3. Specifies any deviations from these standards with justification

### In Every Claude Code Session

Every session starts by reading:
1. `ENGINEERING_STANDARDS.md` (this file)
2. `AGENTS.md` (repo-specific)
3. The `code-audit` skill (pre-commit review framework)

Claude Code must cite specific sections of these documents when making decisions.

### In Every PR Review

Reviewers apply the Section 9 checklist. Deviations are approved or requested-for-change.

### In Every Onboarding

New engineers read this document as part of day-one onboarding. Expected to cite it
in their first PR review.

---

## 12. Version and Maintenance

**Version:** 1.0.0 (2026-04-16)

**Based on:** Wingtip Mobile reference implementation

**Owner:** Ivan Sivak (CTO), Callen Majin (CEO)

**Next review:** 2026-07-16 (quarterly)

**Change log:**
- 2026-04-16: Initial version. Extracted from Wingtip codebase principles.

---

*This document lives in: `[each-repo]/ENGINEERING_STANDARDS.md`*

*Canonical version lives in: `github.com/synthreo/engineering-standards` (TBD)*

*Report violations via GitHub issue in the affected repo.*
