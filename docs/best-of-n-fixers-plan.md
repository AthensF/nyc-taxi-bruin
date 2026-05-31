# Plan: Best-of-N Parallel Fixers with LLM Picker

## Context

The current self-healing ETL pipeline runs a **single Fixer agent** per attempt. If the Reviewer rejects the fix, the loop retries with the same fixer — getting one shot per cycle and relying entirely on prior feedback to improve.

The Anthropic multi-agent research blog shows a better pattern: **parallelize the creative work** (many agents generate candidates simultaneously), then use a **judge** to select the strongest one before any gating review. This shifts the loop from "iterate until good enough" toward "generate a diverse set, pick the best, ship it."

For this pipeline: instead of one Haiku fixer per attempt, spawn **N fixers in parallel** (each with a different persona/temperature bias), let an LLM Picker select the winner, then send only that winner to the Reviewer. Fewer round-trips, higher first-pass quality.

---

## What Changes

### 1. `agents/contracts.py`

**Add to `RepairState`:**
```python
candidates: NotRequired[list[FixCandidate]]   # pool output (N raw fixes)
picker_verdict: NotRequired[PickerVerdict]     # which was selected + why
```

**New dataclass `PickerVerdict`:**
```python
@dataclass
class PickerVerdict:
    selected_index: int          # 0-based index into candidates[]
    rationale: str               # why this one won
    scores: list[dict]           # per-candidate notes, e.g. [{"minimality": "good"}, ...]
```

---

### 2. `agents/prompts.py`

**Add `PICKER_SYS`** — the committee-chair judge prompt:

```
You are a SQL code-review committee chair. You will receive N fix candidates for the same
pipeline failure. Your job: select the single best fix to ship.

Evaluate each candidate on:
1. CORRECTNESS — does it address the root cause and acceptance criteria?
2. MINIMALITY — smallest correct change (no unrelated rewrites)
3. NO DEAD CODE — no fallbacks or COALESCEs masking the real fix
4. BLAST RADIUS — prefer "low" blast radius

Output exactly:
SELECTED: <0-based index>
RATIONALE: <one sentence why this candidate wins>
SCORES: <JSON array of one-line notes per candidate>
```

---

### 3. `agents/llm.py`

**New function `fixer_pool(state, attempt_n, n) -> list[FixCandidate]`:**
- Uses `ThreadPoolExecutor(max_workers=n)` to call the existing `fixer()` logic N times in parallel
- Each call gets a slightly different `FIXER_SYS` prefix (conservative / balanced / aggressive persona) to diversify outputs
- Returns a `list[FixCandidate]` of length N

**New function `picker(state) -> PickerVerdict`:**
- Formats all candidates from `state["candidates"]` into a numbered list
- Calls Claude with `PICKER_SYS` + the formatted candidates
- Parses `SELECTED:` and `RATIONALE:` from response
- Returns `PickerVerdict(selected_index=..., rationale=..., scores=[...])`

**`POOL_SIZE` env var:** default `3`, read at module import time.

**Persona variants for diversity (added to `FIXER_SYS` prefix per slot):**
| Slot | Prefix added |
|------|-------------|
| 0 | "Be conservative: smallest possible change." |
| 1 | "Be balanced: clean and correct." |
| 2 | "Be thorough: ensure edge cases are handled." |

---

### 4. `agents/graph.py`

**Replace the single `fix` node with two nodes:**

```python
@op
def fix_pool(state: RepairState, deps: Deps) -> dict:
    n = state.get("attempts", 0) + 1
    candidates = deps.fixer_pool(state, n)
    return {"candidates": candidates, "attempts": n}

@op
def pick_best(state: RepairState, deps: Deps) -> dict:
    verdict = deps.picker(state)
    winner = state["candidates"][verdict.selected_index]
    return {"fix": winner, "picker_verdict": verdict}
```

**Updated `Deps`:**
```python
@dataclass
class Deps:
    diagnoser: ...
    fixer_pool: Callable[[state, attempt_n], list[FixCandidate]]  # replaces fixer
    picker: Callable[[state], PickerVerdict]                       # new
    reviewer: ...
    deployer: ...
    verifier: ...
    quality_assessor: ...
    gateway: ...
    max_attempts: int
    quality_min: float
```

**Updated graph edges:**
```
diagnose → fix_pool → pick_best → review → [approve→assess_quality | reject→fix_pool | exhaust→needs_human]
assess_quality → [gate_reject→fix_pool | pass→deploy]
deploy → verify → END
needs_human → END
```

All routing logic in `route_after_review` and `_quality_reject` is unchanged — they read `state["fix"]` and `state["attempts"]` which still exist.

**Updated `run_repair` plain-Python loop:**
```python
# Replace:  state.update(deps.fixer(state, n))
# With:
candidates = deps.fixer_pool(state, n)
state["candidates"] = candidates
verdict = deps.picker(state)
state["fix"] = candidates[verdict.selected_index]
state["picker_verdict"] = verdict
```

---

### 5. `tests/test_agents.py`

**New scripted fixtures:**
```python
def scripted_fixer_pool(state, attempt_n, n=3) -> list[FixCandidate]:
    # Returns [HALF_SQL, GOOD_SQL, DEAD_SQL] so picker has something to choose from
    ...

def scripted_picker_select_best(state) -> PickerVerdict:
    # Always selects index 1 (GOOD_SQL) — the correct, minimal fix
    return PickerVerdict(selected_index=1, rationale="minimal and correct", scores=[])
```

**New / updated tests:**

| Test | What it verifies |
|------|-----------------|
| `test_fix_pool_returns_n_candidates` | `fixer_pool` returns exactly N `FixCandidate` objects |
| `test_pick_best_selects_winner` | `pick_best` writes `state["fix"]` = `candidates[verdict.selected_index]` |
| `test_run_repair_converges_after_one_reject` *(update)* | Use `scripted_fixer_pool` + `scripted_picker_select_best` in `Deps`; healed=True still passes |
| `test_run_repair_gives_up_without_deploying` *(update)* | Pool always returns HALF_SQL variants; exhaust → needs_human |
| `test_milestones_include_picker_verdict` | `PickerVerdict` is emitted and typed |
| `test_llm_picker_selects_minimal_fix` *(llm-marked)* | Real picker rejects DEAD_SQL (has COALESCE), picks GOOD_SQL |

---

## Graph Before vs. After

**Before:**
```
diagnose → fix → review → [approve→assess_quality | reject→fix | exhaust→needs_human]
```

**After:**
```
diagnose → fix_pool → pick_best → review → [approve→assess_quality | reject→fix_pool | exhaust→needs_human]
```

---

## Verification

1. `pytest tests/test_agents.py -x` — all deterministic tests pass (no API key needed)
2. `python agents/run_demo.py` — full live run; Weave trace shows `fix_pool` node with 3 candidates + `pick_best` verdict before Reviewer sees anything
3. `python agents/run_demo.py --force-reject` — Picker selects winner each attempt; Reviewer still gets one clean candidate per round

---

## Effort Estimate

| File | Change size |
|------|------------|
| `contracts.py` | +15 lines (PickerVerdict + 2 state fields) |
| `prompts.py` | +12 lines (PICKER_SYS) |
| `llm.py` | +40 lines (fixer_pool, picker, persona variants) |
| `graph.py` | +25 lines (two new nodes, updated edges, updated Deps, updated run_repair) |
| `tests/test_agents.py` | +60 lines (new fixtures + 4 new tests, 2 updated) |

Small, surgical changes. No existing node logic is touched — only the `fix` node is split in two and the `Deps` contract is extended.
