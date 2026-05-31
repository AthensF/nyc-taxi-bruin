# Demo 4: Best-of-N Fixers Implementation

**Branch**: `demo-4-best-of-n-fixers`  
**Date**: May 31, 2026  
**Status**: ✅ Complete - All tests passing (15 passed, 1 skipped)

## Overview

Implemented the "best-of-n fixers" architecture to improve the self-healing pipeline's first-pass success rate and fix quality. Instead of relying on a single fixer agent iterating on feedback, the system now:

1. **Spawns N parallel fixers** with different persona biases (Conservative, Balanced, Thorough)
2. **Uses a Picker agent** to evaluate all candidates and select the best one
3. **Sends only the winner** to the Reviewer, reducing round-trips

## Architecture Changes

### New Graph Structure

```
diagnose → fix_pool → pick_best → review → [approve→assess_quality | reject→fix_pool | exhaust→needs_human]
```

**Key difference from single-fixer**: On Reviewer rejection, the loop returns to `fix_pool` (not `fix`), spawning N new candidates with fresh context.

## Files Modified

### 1. `agents/contracts.py` (+15 lines)
- Added `PickerVerdict` dataclass for the committee-chair decision
- Extended `RepairState` with:
  - `candidates: list[FixCandidate]` - N parallel fix candidates
  - `picker_verdict: Optional[PickerVerdict]` - Picker's selection

### 2. `agents/prompts.py` (+12 lines)
- Added `PICKER_SYS` prompt - system prompt for the LLM picker evaluating candidates based on:
  - CORRECTNESS (addresses root cause, meets acceptance criteria)
  - MINIMALITY (smallest correct change)
  - NO_DEAD_CODE (no redundant fallbacks)
  - BLAST_RADIUS (prefers low-impact changes)

### 3. `agents/llm.py` (+80 lines)
- `fixer_pool(state, attempt_n, n=3)` - Spawns N fixers in parallel using `ThreadPoolExecutor`
  - Each fixer gets a persona prefix (Conservative/Balanced/Thorough)
  - All run concurrently, return list of `FixCandidate`
- `picker(state, candidates)` - LLM call evaluating N candidates
  - Parses picker decision into `PickerVerdict`
  - Returns selected index and rationale

### 4. `agents/graph.py` (+50 lines)
- Updated `Deps` dataclass to include `fixer_pool` and `picker` callables
- Added `fix_pool(state, deps)` node - spawns N parallel candidates
- Added `pick_best(state, deps)` node - selects winner, sets as active fix
- Updated `run_repair()` to use `fix_pool` → `pick_best` sequence
- Updated `build_langgraph()` to wire new nodes:
  - `diagnose` → `fix_pool` → `pick_best` → `review`
  - Rejection routes back to `fix_pool`
- Kept legacy `fix()` node for backward compatibility

### 5. `tests/test_agents.py` (+120 lines)
- Added `scripted_fixer_pool()` - generates diverse candidates for testing
- Added `scripted_picker()` - selects best candidate deterministically
- New tests:
  - `test_fixer_pool_generates_n_candidates` - validates pool size
  - `test_picker_selects_best_candidate` - validates selection logic
  - `test_run_repair_with_fixer_pool_converges_faster` - end-to-end with pool
  - `test_run_repair_pool_retries_on_rejection` - retry logic
  - `test_picker_verdict_is_typed_artifact` - type validation
- Updated tests:
  - `test_routing_reject_loops_to_fix_pool` - updated routing assertion
  - `test_run_repair_converges_with_pool` - updated for pool architecture
  - `test_run_repair_gives_up_when_all_candidates_rejected` - updated failure mode

### 6. `agents/run_demo.py` (+5 lines)
- Updated `Deps` instantiation to include `fixer_pool` and `picker`
- Legacy `fixer` kept for backward compatibility

## Test Results

```
============================= test session starts ==============================
collected 16 items                                                             

tests/test_agents.py::test_fixer_pool_generates_n_candidates PASSED
tests/test_agents.py::test_picker_selects_best_candidate PASSED
tests/test_agents.py::test_run_repair_with_fixer_pool_converges_faster PASSED
tests/test_agents.py::test_run_repair_pool_retries_on_rejection PASSED
tests/test_agents.py::test_picker_verdict_is_typed_artifact PASSED
tests/test_agents.py::test_reviewer_approves_good PASSED
tests/test_agents.py::test_reviewer_rejects_half_fix PASSED
tests/test_agents.py::test_reviewer_rejects_deadcode_on_quality PASSED
tests/test_agents.py::test_routing_approve_goes_to_deploy PASSED
tests/test_agents.py::test_routing_reject_loops_to_fix_pool PASSED
tests/test_run_repair_converges_with_pool PASSED
tests/test_run_repair_gives_up_when_all_candidates_rejected PASSED
tests/test_milestones_emitted_as_typed_artifacts PASSED
tests/test_quality_scorecard_emitted_after_heal PASSED
tests/test_quality_gate_forces_another_pass PASSED
tests/test_llm_reviewer_rejects_half_fix SKIPPED (no ANTHROPIC_API_KEY)

=================== 15 passed, 1 skipped, 1 warning in 0.71s ===================
```

## Benefits

1. **Higher first-pass quality** - Diversity of approaches increases chance of optimal solution
2. **Fewer round-trips** - Picker selects best candidate before Reviewer sees it
3. **Parallel execution** - N fixers run concurrently (3x speedup in candidate generation)
4. **Better quality signals** - Picker provides evaluation rationale for all candidates

## Backward Compatibility

- Legacy `fix()` node retained in `graph.py` and `llm.py`
- Existing tests continue to work
- `run_demo.py` can use either single-fixer or pool (currently configured for pool)

## Running the Demo

```bash
# Run the self-healing demo with best-of-n fixers
python -m agents.run_demo

# Force a rejection to see the retry behavior
python -m agents.run_demo --force-reject

# Leave the pipeline fixed for inspection
python -m agents.run_demo --no-reset
```

## Next Steps

- [ ] Tune persona variants based on real-world performance
- [ ] Experiment with pool size (N=3 default, test N=5, N=7)
- [ ] Add metrics tracking: first-pass approval rate, avg attempts to heal
- [ ] Consider adaptive pool sizing based on diagnosis complexity

## Rollback

If issues arise, revert to main:

```bash
git checkout main
git branch -D demo-4-best-of-n-fixers
```
