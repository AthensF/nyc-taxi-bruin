# Self-Healing ELT: Multi-Agent Pipeline Repair (Hackathon Plan)

## Context

We have a working bruin ELT pipeline (`nyc-taxi-pipeline`, local DuckDB). The hackathon goal is to **demonstrate multi-agent orchestration in ELT**: when an upstream **schema change** breaks the pipeline, three goal-driven LLM agents — **Diagnoser → Fixer → Reviewer** — diagnose, fix, review, and **negotiate** a fix, then deploy it — with the whole conversation observable in **WandB Weave**.

The judging story has three legs: (1) real **orchestration** — agents genuinely hand off and the Reviewer can veto the Fixer; (2) **technical execution** — a fix only ships after it re-validates green; (3) **utility** — "self-healing that can't silently corrupt your data." Weave is the observability layer that makes the negotiation visible (the $1k Weave angle).

**Decisions locked in:** LangGraph framework · **3-agent goal-driven loop** (Diagnoser → Fixer → Reviewer), deploy on approval · breakage = column rename `payment_type → payment_method` · **done heuristic** = the healed pipeline can still answer *"what is the highest-grossing day during the month?"*

---

## The pipeline (as-is)

```
ingestion.trips (python)  ┐
                          ├─> staging.trips (sql) ─> reports.trips_report (sql)
ingestion.payment_lookup  ┘
```

- `zoomcamp/pipeline/assets/staging/trips.sql` references the upstream column `PAYMENT_TYPE` in **two places**:
  - line 72: `PAYMENT_TYPE as payment_type,`
  - line 78: `LEFT JOIN ingestion.payment_lookup p ON d.PAYMENT_TYPE = p.payment_type_id`
- bruin CLI v0.11.603 is installed (`/Users/athens/.local/bin/bruin`); DuckDB is local (`duckdb.db`, no creds). Working tree already migrated BQ→DuckDB (keep those edits).

## The breakage scenario

Simulate "a new upstream load arrived with a renamed column." Rename `payment_type → payment_method` in the materialized `ingestion.trips` table. `staging.trips` then fails: **`Referenced column "PAYMENT_TYPE" not found`**. The agents must rewrite the staging SQL to use `payment_method`, re-validate, and ship.

**Why inject at the table (not by editing `trips.py` + re-downloading):** re-running the Python ingestion re-downloads parquet from S3 (slow, flaky for a live demo). Instead, seed DuckDB **once**, then inject drift with a fast `ALTER TABLE ... RENAME COLUMN`, and have agents re-run **only downstream** (`staging.trips` + `reports.trips_report`) — a sub-second SQL loop. This keeps every demo iteration fast and deterministic. (We note the `trips.py`-edit variant as a "more realistic" fallback if judges ask.)

---

## Architecture

```
                 LangGraph StateGraph  (3 agents, goal-driven)
  inject_drift ─> diagnose ─> fix ─> review ─┬─(reject)─> fix         (loop, max 3)
                     │                        └─(approve)─> deploy ─> verify(oracle green)
                     └─ proposes the acceptance test the fix must pass ─┘
   every node @weave.op  +  Anthropic calls + MCP tool calls auto-traced by Weave
```

**The agents (LangGraph nodes, each a `@weave.op`) — each defined by a *goal*, not just a task:**

1. **Diagnoser** — *Goal: make the breakage replicable, explainable, and testable.*
   - Replicates the failure (runs bruin, captures the real error), **highlights** the root cause (inspects `DESCRIBE ingestion.trips`, pinpoints `payment_type → payment_method` and the two staging references), and **explains *why*** (the upstream rename orphaned the JOIN key).
   - Crucially, it **proposes the test the Fixer must pass** — a structured acceptance spec: structural checks (no `payment_type` refs; `payment_type_name` not_null contract intact) **plus the business oracle below**. It does *not* write the SQL itself.

2. **Fixer** — *Goal: accomplish the fix the Diagnoser specified.*
   - Takes the diagnosis + the proposed test and produces a patched `staging/trips.sql` (held in-state, not deployed). On a Reviewer rejection it re-attempts against the *same* test plus the Reviewer's feedback.

3. **Reviewer** — *Goal: verify it works AND judge how well it's built.*
   - **Verifies**: does the patch satisfy the Diagnoser's test (re-runs bruin + the oracle query)? **Assesses quality**: minimal, idiomatic, preserves the not_null contracts, no needless churn. Emits `approve | reject` + feedback — the genuine veto that drives the negotiation.

**Done heuristic — the business oracle (the Diagnoser's headline test):**
> *"What is the highest-grossing day during the month?"*
> ```sql
> SELECT pickup_date, SUM(total_amount) AS gross_revenue
> FROM reports.trips_report
> GROUP BY pickup_date
> ORDER BY gross_revenue DESC
> LIMIT 1;
> ```
> The heal is "done" when this returns a single `(pickup_date, gross_revenue)` row with `gross_revenue > 0` and a date inside the loaded month. It's a **behavioral** check — the pipeline must not merely compile, it must still answer a real business question correctly. Used as the parse/acceptance oracle in `verify` and test `IT1`, and as the spec the Diagnoser hands the Fixer.

**Capabilities vs. exposure — a deliberate, minimal observability surface.** The agents *can* touch the world in four ways, but we **don't** make all four first-class traced MCP tools. *Capability* = what an agent needs to act; *exposure* = what we surface as an observability signal in Weave. Exposing every primitive is overkill — it clutters the trace and pays the MCP-plumbing tax for calls that don't advance the story. So we expose only the operations that **produce a signal the agents reason about** — what leads us *to* the error, and what *confirms* the resolution:

**Exposed as traced MCP tools — the evidence trail of the negotiation:**
- `run_bruin(asset, *flags)` → run/validate the pipeline. *The detector + verifier* — its red→green is the spine of the demo and the source of the real error text.
- `inspect_schema(table)` → `DESCRIBE` (names **and** types). *The structural reveal* — the "agent found the drift" moment; catches rename / add / drop / nullability / **type-change (e.g. `date → timestamp`)**.
- `oracle()` → the highest-grossing-day query. *The acceptance verdict* (done-heuristic), surfaced as a **named** checkpoint, not a generic SELECT.

**Internal capability — available to the agents, but NOT a first-class exposed tool:**
- `query_duckdb(sql)` → generic read-only query the diagnosis logic calls under the hood (`oracle` is its one named specialization). Kept for future *semantic*-drift probing; not surfaced in the MVP trace.
- `apply_patch(path, content)` → the single gated mutation. Not a diagnosis function — fires only at `deploy` after approval; a plain local file write, traced **once** there as an *audit* event ("who changed what"), not an ad-hoc agent tool.

**Why this cut:** the Weave story is the *negotiation* (Diagnoser→Fixer→Reviewer LLM exchanges, auto-traced) plus the *evidence* the agents act on (failed run → found drift → passing oracle). Those three exposed ops give exactly that and nothing more; fewer MCP tools also means a cleaner trace and less live-demo surface to break. *Tradeoff, named honestly:* selective exposure means a misbehaving agent could act through an un-traced internal call — a production observability product might trace everything, but for a short (3-hour) demo a legible trace beats an exhaustive one.

**Drift-agnostic, with known blind spots.** The capability set generalizes beyond the rename MVP (`run_bruin` detects, `inspect_schema` localizes *structural* causes incl. type changes, `query_duckdb`+oracle/golden localize *semantic* ones) — adding a breakage type is a new `DriftScenario` + prompt tuning, **not** new tools. **Known limitations (stated, not hidden):** `inspect_schema` is blind to *semantic* drift where the catalog is unchanged — remapped codes (`1=cash → 1=credit`), unit changes (`$ → ¢`), timezone/format shifts, new nulls in nullable columns. Those are caught (if at all) only by the `oracle` + golden comparison; proactive detection needs an expected-schema contract (the *Schema-Contract Sentinel*, expansion).

The LangGraph nodes call the **exposed** tools via a `ClientSession` (same pattern as `weave_mcp_hello/client.py`); Weave patches `FastMCP` + `ClientSession`, so each exposed call lands in the trace tree beside the LLM calls — no extra instrumentation.

> **This is only the *tool* axis (Axis 1).** The agents' *reasoning* milestones — diagnose → propose test → apply fix → review verdict → verify — are a separate, deliberately **rich** exposure surface (Axis 2), traced as named `@weave.op` spans with typed outputs. See **Observability › Axis 2**.

**Why patch-in-state until approval:** the file is only mutated by `apply_patch` at the `deploy` node, so a rejected fix never touches the repo — clean story and clean git diff for the demo.

---

## Files to create (all NEW; existing pipeline untouched except the demo edit to `staging/trips.sql` made *by the agent*)

New top-level `agents/` dir, **seeded from the existing `weave_mcp_hello/` folder** (already a working Weave+MCP integration against `afitzc-mit/weave-mcp-hello`):

| File | Purpose |
|---|---|
| `agents/requirements.txt` | `langgraph`, `langchain-anthropic`, `weave`, `anthropic`, `mcp[cli]`, `duckdb`, `python-dotenv` |
| `agents/bruin_mcp_server.py` | **Adapted from `weave_mcp_hello/server.py`.** `FastMCP("BruinOps")` + `weave.init(...)`; exposes only the **signal** tools `run_bruin`, `inspect_schema`, `oracle` as `@mcp.tool()`s. Generic `query_duckdb` is an internal helper; `apply_patch` is a local gated write traced once for audit (see *Capabilities vs. exposure*). |
| `agents/mcp_client.py` | **Adapted from `weave_mcp_hello/client.py`.** Thin `ClientSession` wrapper the nodes use to call the tools (so Weave traces them). |
| `agents/graph.py` | LangGraph `StateGraph`: state schema + `diagnose`, `fix`, `review`, `deploy`, `verify` nodes, conditional edges (`review → fix` on reject), `@weave.op` on each |
| `agents/prompts.py` | System prompts for **Diagnoser, Fixer, and Reviewer** — each stated as a *goal* + an output contract (the Diagnoser's includes emitting the acceptance test) |
| `agents/run_demo.py` | Entrypoint: `weave.init("afitzc-mit/self-healing-elt")`, inject drift, invoke the graph, print the trace link |
| `agents/contracts.py` | **The seam.** Shared `RepairState` + the typed **milestone/handoff objects** `Diagnosis` / `TestSpec` / `FixCandidate` / `ReviewVerdict` / `VerifyResult` / `Authorization` — carrying `evidence`, `acceptance_criteria`, `blast_radius`, `confidence`, `audit_id` (these are what make the Axis-2 spans read like an incident thread), plus `ToolResult`, the `ToolGateway` protocol, the `DriftScenario` registry, and the oracle SQL. Both workstreams import only this. |
| `scripts/seed.sh` | seed DuckDB once on a tiny window (`bruin run … --start 2022-01-01 --end 2022-02-01 --var taxi_types='["yellow"]'`) |
| `scripts/inject_drift.sql` | `ALTER TABLE ingestion.trips RENAME COLUMN payment_type TO payment_method;` |
| `scripts/reset.sh` | `git checkout` staging SQL + rename column back + rebuild downstream (idempotent, re-run the demo) |
| `scripts/golden/` | known-good `reports.trips_report` snapshot — regression ground truth for the (stretch) Regression Guard + test `IT4` |
| `tests/test_agents.py` | pytest matrix for each agent (see **Test cases** below) |
| `agents/README.md` | one-screen runbook for the team + judges |

**Reuse, don't rebuild:** (1) seed the MCP server/client from your **`weave_mcp_hello/`** PoC — it already proves `weave.init` + FastMCP + ClientSession tracing works end-to-end against your W&B account; (2) lean on the existing bruin CLI for all execution/validation (it already enforces the column `not_null` checks in `staging/trips.sql` — that *is* our regression signal); (3) lean on git for the audit trail (`git diff` shows exactly what the agent changed).

---

## Observability (WandB Weave) — building on your `weave_mcp_hello/` PoC

- `weave.init("afitzc-mit/self-healing-elt")` once (in both the MCP server and `run_demo.py`, matching the pattern your `weave_mcp_hello/{server,client}.py` already use). Requires `WANDB_API_KEY` (your existing `afitzc-mit` entity).
- **Two exposure axes — minimal on tools, rich on the *workflow*:**

  **Axis 1 · World-tool signals (kept minimal — 3).** Weave patches `FastMCP` + `ClientSession`, so the exposed signal tools (`run_bruin` / `inspect_schema` / `oracle`) are traced. Generic `query_duckdb` stays internal; `apply_patch` is traced once at deploy as an audit event. (At 3h these may run via `DirectGateway` `@weave.op` functions instead of MCP — same trace, less plumbing; see timeline.)

  **Axis 2 · Workflow / decision milestones (the multi-agent story — expose richly).** **Surface engineering *artifacts exchanged between specialists*, not raw chain-of-thought.** Each milestone is a *handoff document* — an incident report → a PR description → a senior-engineer review verdict — carrying evidence, acceptance criteria, blast-radius, and a decision. That reads as professional collaboration (evidence, disagreement, validation — what people care about in production systems), not model navel-gazing. Each is a *named* `@weave.op` returning a **typed object**:
  - `diagnose` → `Diagnosis{root_cause, evidence[error_excerpt, schema_diff], references[file:line], acceptance_criteria[]}` — *the incident report:* "I reproduced it, here's the error, the column `payment_method` exists but `payment_type` doesn't, here are the two refs (projection L72, join L78), and here's what a fix must satisfy."
  - `propose_test` → `TestSpec{oracle_sql, expected_shape, structural_assertions}` — **"come up with the test case"** (a sub-`@weave.op` inside the Diagnoser → its own span, no new graph node).
  - `propose_fix` → `FixCandidate{attempt_n, files_changed, summary, changes[], blast_radius, criteria_addressed[✅/❌]}` — **the PR description:** what changed, "no other logic modified," blast radius *low*, an acceptance-criteria checklist.
  - `review` → `ReviewVerdict{decision, correctness_findings, quality_findings, confidence, rationale}` — **the senior-engineer review:** rejects a half-fix ("JOIN still references `PAYMENT_TYPE`"), rejects *dead code* ("`COALESCE(PAYMENT_METHOD, PAYMENT_TYPE)` — fallback is dead, simplify"), or approves *with a confidence*. Attached to the fix call as a Weave **feedback/score** so decision + confidence become filterable metrics.
  - `verify` → `VerifyResult{bruin_status, oracle_status, oracle_answer, healed}` — *the behavioral proof:* "oracle returned `2022-01-21 | 187,341.22` — positive, in-month → healed." Shows you didn't just compile SQL, you proved the business question still works.
  - `deploy` → `Authorization{decision:APPROVE, evidence[validation, oracle, contracts], audit_id}` then a separate **execution** event — a clean *proposal-vs-execution* split ("Deployment authorized" → "Applying patch to `staging/trips.sql`, audit `8f4b7e`").
  - `negotiation_round(n)` *(wrapper)* → groups `propose_fix → review` for attempt N with an `attempt` attribute, so the loop is legible at a glance.

  These typed objects live in `contracts.py` / `RepairState`; the raw Anthropic prompt/response is auto-patched *underneath* each named span (available on drill-in, not the headline). **Axis 2 is nearly free (decorators + structured returns) and is the part to *not* cut even at 3h.**
- Result: the Weave trace reads like an **incident timeline**, not a chain-of-thought dump:
  ```text
  Diagnose      root cause identified (payment_type → payment_method)
  Propose Test  oracle defined
  Fix #1        patch proposed
  Review #1     REJECTED — join still references PAYMENT_TYPE
  Fix #2        patch proposed
  Review #2     APPROVED  (confidence 0.91)
  Deploy        patch applied · audit 8f4b7e
  Verify        oracle green · 2022-01-21 | 187,341.22
  ```
  Milestones as named spans, the 3 tool signals interleaved, the verdict+confidence as a score. **This is the money shot** — professional collaboration, evidence, disagreement, and validation.
- **Validate the wiring early** by running the existing `weave_mcp_hello` demo (`python client.py server.py`) to confirm the W&B login/entity works before building on it.

---

## Negotiation beats — the demo's memorable middle

Nobody's impressed when agents agree instantly. The artifacts above let you stage *disagreement with evidence*. In priority order:

1. **Correctness reject (core).** Fixer ships a half-fix — projection updated, JOIN still references `PAYMENT_TYPE`. Reviewer rejects with the exact reason; validation still red; Fixer re-fixes. The `Fixer → Reviewer ❌ → Fixer` loop is *the* money shot.
2. **Quality reject (high value, low cost).** Fixer produces *working but ugly* SQL — `COALESCE(PAYMENT_METHOD, PAYMENT_TYPE)`. Validation passes, but the fallback is dead code (the column is gone). Reviewer rejects on **quality** and asks to simplify — a senior engineer, not a test runner.
3. **Behavioral proof.** Reviewer cites the actual oracle row (`2022-01-21 | 187,341.22`): "we proved the business question still works," not just "it compiles."
4. **Risk / confidence.** Reviewer emits a confidence + recommendation — *Low: payment-lookup cardinality changed 18% → recommend human review* — routing to `needs_human` instead of deploying. Turns the story from syntax to **trust**.
5. **Cross-check (advanced/stretch).** Reviewer approves *before* the oracle ran; the Diagnoser challenges — "acceptance criteria not fully evaluated, the oracle wasn't executed; withhold approval." Agents holding *each other* accountable, not a one-way pipeline.
6. **Deploy authorization.** Explicit `APPROVE` + evidence list + `audit_id`, then a separate execution event — clean proposal-vs-execution separation.

Beats **1–4 are built in the MVP** (they fall straight out of the typed verdicts + the oracle); **5** is a stretch back-edge (`review → diagnose` cross-check); **6** is the deploy artifact. Pre-stage beat 1 (seed a bad first patch) so the on-stage veto is guaranteed.

---

## Build timeline (ruthless 3h scoping)

**Big cut to fit 3h:** skip the MCP `ClientSession` plumbing for the MVP — run the 3 tools as plain `@weave.op` functions behind `DirectGateway` (still fully traced in Weave, far less to break live). The MCP tool-server becomes a stretch. The **Axis-2 workflow-milestone exposure is built, not cut — it's the demo's value.** Assumes DuckDB is already seeded (the 182MB `duckdb.db` exists).

1. **Setup + prove the break (25m)** — venv + `pip install -r agents/requirements.txt`; export `ANTHROPIC_API_KEY` + `WANDB_API_KEY`; run `weave_mcp_hello` once to confirm Weave logs under `afitzc-mit`. **Pre-flight by hand:** `inject_drift.sql` → `bruin run <staging> --downstream` fails with the column error → `reset.sh`. Don't write an agent until this is proven.
2. **Traced tools + DirectGateway (25m)** — `run_bruin`, `inspect_schema`, `oracle` as plain `@weave.op` functions behind the `ToolGateway` protocol. No MCP. Smoke-test each.
3. **3 agents + structured milestones + graph (45m)** — `diagnose` (emits `Diagnosis` + `TestSpec` via a sub-op), `fix` (emits `FixCandidate`), `review` (emits `ReviewVerdict`, attached as a Weave score); wire LangGraph `diagnose → fix → review → deploy → verify` with `review → fix` on reject + `max_attempts`. Getting the *typed outputs* flowing IS the Axis-2 exposure.
4. **Weave exposure pass (30m)** — confirm each milestone is a named span carrying its typed object, the `negotiation_round` wrapper groups attempts, the reject→re-fix path is visible, and the Reviewer score shows. **This is the money shot — spend the time here.**
5. **Deploy + verify + oracle (25m)** — `apply_patch` (gated local write, audit-traced once) then `verify` asserts bruin green **and** the highest-grossing-day oracle returns a valid row. End-to-end `run_demo.py`.
6. **Demo dry-run (30m)** — rehearse: green → inject → red → agents negotiate (watch the trace) → green; show `git diff` as the receipt. Pre-stage a deliberately *bad* first patch for a guaranteed on-stage Reviewer veto.

*(≈180m. Stretch only if ahead: swap DirectGateway → the MCP server/client for the `weave_mcp_hello` tool-layer trace; Regression Guard vs `scripts/golden/`; a 2nd drift scenario.)*

---

## Verification (end-to-end)

1. **Baseline green:** `bruin validate ./zoomcamp/pipeline/pipeline.yml` passes; staging table has a `payment_type` column.
2. **Inject + confirm red:** apply `scripts/inject_drift.sql`; `bruin run <staging asset path> --downstream` fails with `Referenced column "PAYMENT_TYPE" not found`.
3. **Run the agents:** `python agents/run_demo.py` → graph converges to `approve`, deploy writes the 2-line fix to `staging/trips.sql`, verify re-runs bruin to **green**.
4. **Business oracle (the done heuristic):** the highest-grossing-day query returns a single `(pickup_date, gross_revenue)` row with `gross_revenue > 0` and a date inside the loaded month — i.e. the pipeline can again answer *"what is the highest-grossing day during the month?"*
5. **Audit trail:** `git diff zoomcamp/pipeline/assets/staging/trips.sql` shows exactly `PAYMENT_TYPE → PAYMENT_METHOD` in both spots and nothing else.
6. **Observability:** open the Weave project URL printed by `run_demo.py`; confirm the trace shows Diagnoser → Fixer → Reviewer hand-offs, at least one reject→re-fix, nested Anthropic calls, and the final deploy/verify.
7. **Repeatable:** `scripts/reset.sh` restores baseline so the demo can be re-run cleanly.

---

## Parallelization — running this across multiple Claude chats

This build is designed to run as **separate Claude Code chats in parallel** (saves context/tokens, keeps each agent focused). Three rules make it safe *and* keep cohesion:

1. **Freeze the seam first.** One file — `agents/contracts.py` — is the shared interface (typed `RepairState`, the milestone/handoff objects, the `ToolGateway` protocol, the `DriftScenario` registry, the oracle SQL, and a `FakeGateway`). It is written **once, up front, committed to `main`** *before* A/B start, then treated as **frozen**: no workstream edits it unilaterally — a needed change goes through *you* (the human integrator) and is re-shared. This single rule is what guarantees the parts fit back together.
2. **Disjoint file ownership.** No two chats touch the same file (table below). That's what removes merge conflicts.
3. **Separate branches/worktrees.** Each chat works on its own git branch (ideally its own `git worktree`). `duckdb.db` is owned by **A** during dev; **B never touches the real DB** (it uses `FakeGateway`) until integration.

**Foundation (do this FIRST — ~15 min, owned by the orchestrating chat).** Create `agents/contracts.py`, `agents/requirements.txt`, the `agents/`+`tests/` skeleton, a `FakeGateway` returning canned fixtures (the recorded error string + post-drift schema), **and the merge gate** (`tests/test_integration.py` + `scripts/check_merge.sh` + `INTEGRATION.md`). Commit to `main`. This unblocks B immediately, hands A its scenario/oracle constants, and gives every chat a shared definition of "mergeable." **B is blocked until this is on `main`.**

**Part A — Data & Pipeline (no LLM, fully standalone)**
- Exclusive files: `scripts/seed.sh`, `scripts/inject_drift.sql`, `scripts/reset.sh`, `scripts/golden/` (+ capture helper), `tests/test_scenario.py`.
- Job: make the break replicable + reversible; capture golden output; confirm the oracle SQL (from `contracts.py`) returns a valid row on healthy data.
- Contract it MUST honor: `inject` then `bruin run <staging> --downstream` fails with stderr containing `"payment_type"`; `reset` restores green. (This is what B's `DT1 reproduce` asserts against.)
- DoD: `seed → inject → red → reset → green` by hand, repeatably; `pytest tests/test_scenario.py` green; `scripts/golden/expected_report.csv` committed.

**Part B — Agents, Orchestration & Weave (the build)**
- Exclusive files: `agents/tools.py` (DirectGateway: `run_bruin`/`inspect_schema`/`oracle`/`apply_patch` as `@weave.op`), `agents/graph.py`, `agents/prompts.py`, `agents/run_demo.py`, `tests/test_agents.py`. Stretch: `agents/bruin_mcp_server.py`, `agents/mcp_client.py`.
- Job: the 3 goal-driven agents + LangGraph loop + the Axis-2 typed-milestone exposure (incident-timeline trace).
- Decoupling: depends only on `contracts.py`. Develop against `FakeGateway`; swap to the real gateway at integration with **zero graph changes**. Do not run bruin / touch `duckdb.db` during dev.
- DoD: against `FakeGateway`, `python agents/run_demo.py` converges `approve` and prints a patched SQL string; Weave shows the named milestones + a reject→re-fix + the Reviewer score; `pytest tests/test_agents.py -m "not llm"` green.

**Part C — Observability polish & Demo (optional 3rd chat; non-overlapping)**
- Exclusive files: `agents/README.md`, `docs/demo-run-of-show.md`, `scripts/weave_smoke.py`.
- Job: confirm the Weave trace renders the 6 negotiation beats, write the run-of-show, capture screenshots. Reads B's span names from this plan; does **not** edit B's code.
- DoD: a rehearsed ≤3-min demo script + a confirmed Weave trace. *At 3h, C usually folds into B — only spin up a 3rd chat with a spare person.*

**Ownership table (no cell shared → no conflicts):**
| File / artifact | Foundation | A | B | C |
|---|:--:|:--:|:--:|:--:|
| `agents/contracts.py`, `requirements.txt`, skeleton, `FakeGateway` | ● | | | |
| `tests/test_integration.py`, `scripts/check_merge.sh`, `INTEGRATION.md` (merge gate) | ● | | | |
| `scripts/{seed.sh, inject_drift.sql, reset.sh}`, `scripts/golden/` | | ● | | |
| `tests/test_scenario.py` | | ● | | |
| `agents/{tools, graph, prompts, run_demo}.py` | | | ● | |
| `tests/test_agents.py` | | | ● | |
| `agents/{bruin_mcp_server, mcp_client}.py` *(stretch)* | | | ● | |
| `agents/README.md`, `docs/demo-run-of-show.md`, `scripts/weave_smoke.py` | | | | ● |
| `duckdb.db` (runtime state, dev) | | ● | | |

**Integration (you own this).** When A's DoD and B's DoD are both green: on one branch, point B's real gateway at A's scripts + `duckdb.db`, run `python agents/run_demo.py` end-to-end, then the full **Verification** section. Merge order: `main` (foundation) → A → B → C. The frozen `contracts.py` means this is a swap, not a rewrite.

**Handoff prompts to start each chat are in the Appendix at the end of this file.**

---

## Integration & merge gate — proving the parts fit

Parallel work fails *at the seam*, so the seam gets an automated gate. The cohesion guarantee rests on one fact: **the `FakeGateway` that B builds against must be byte-faithful to A's real pipeline.** The gate proves exactly that, plus that nobody quietly forked the contract. It is **owned by the Foundation step** (neutral + shared); each chat runs it before declaring done, and the human runs it once on the integration branch.

**`scripts/check_merge.sh` — one command → one verdict:**
1. **Contract unchanged.** `git diff main -- agents/contracts.py` is empty. If anyone edited the frozen seam → **FAIL loudly** (the #1 cohesion risk).
2. **A's DoD.** `pytest tests/test_scenario.py` — real pipeline: inject→red→reset→green, oracle valid.
3. **B's DoD.** `pytest tests/test_agents.py -m "not llm"` — graph converges on `FakeGateway`, milestones emitted.
4. **Seam parity (the crux).** `pytest tests/test_integration.py -m parity`:
   - inject → REAL `run_bruin(staging)` stderr contains `expected_error_substring`; assert `FakeGateway().run_bruin(...)` yields the **same** substring → the fake's error is faithful to reality.
   - REAL `inspect_schema("ingestion.trips")` shows `payment_method`, not `payment_type`; assert `FakeGateway` returns the same shape; then `reset` → green.
5. **End-to-end heal (needs A+B+keys).** `pytest tests/test_integration.py -m "integration and llm"` → REAL gateway: inject → `run_demo` → approve/deploy → bruin green **and** oracle valid → `git diff` shows only `payment_type→payment_method` → reset.
6. Prints a ✅/❌ checklist and a non-zero exit on any failure.

**Definition of "mergeable":** steps **1–4 green on each agent's own branch** (no API key needed — A and B can each self-certify in isolation), and **1–5 green on the integration branch**. Merge order stays `main (foundation) → A → B → C`. If a parity test fails, the fix lives in A's pipeline or the Foundation's `FakeGateway` fixtures — **never** a silent edit to a downstream consumer. `INTEGRATION.md` carries the same checklist as tick-boxes each chat marks when its branch passes the gate, so "are we ready to merge?" is answered by the gate, not by vibes.

---

## Test cases per agent

Tests live in `tests/test_agents.py` (pytest). Deterministic tests run with no API key; LLM-dependent ones are marked `@pytest.mark.llm` and skipped unless `ANTHROPIC_API_KEY` is set. **Each agent is judged against its goal.**

**Diagnoser — goal: make the break replicable, explainable, and testable.**
- `DT1 reproduce`: after inject, the reproduce step returns `ok == False` and stderr contains `expected_error_substring`. *Proves it triggers the real failure, not a hallucinated one.* ← your "replicate the error" case.
- `DT2 locate-and-explain`: `inspect_schema("ingestion.trips")` shows `payment_method` and **not** `payment_type`; the diagnosis text names the renamed column and the two staging references, and states **why** (orphaned JOIN key).
- `DT3 propose-test`: the Diagnoser emits a runnable acceptance spec that includes the **highest-grossing-day oracle** (parseable SQL + an expected shape: one row, `gross_revenue > 0`). ← "propose a test for the Fixer to pass."
- `DT4 no-false-alarm`: on a healthy pipeline it reports `noop` instead of inventing a problem.

**Fixer — goal: accomplish the proposed fix (pass the Diagnoser's test).**
- `FT1 satisfies-diagnosis`: proposed SQL no longer references `payment_type` and references `payment_method` in **both** the projection and the JOIN.
- `FT2 passes-oracle`: after applying the Fixer's SQL to a scratch run, the highest-grossing-day query returns a valid `(pickup_date, gross_revenue>0)` row. *(The Diagnoser's test, now green.)*
- `FT3 preserves-contract`: output keeps the `@bruin` header and all five `not_null` checks; `payment_type_name` still populated via the lookup.
- `FT4 minimal-diff`: the patch changes only what the diagnosis requires (diff touches ~2 lines).

**Reviewer — goal: verify it works AND judge quality.** (the negative/veto tests are the point)
- `RT1 approve-good`: a correct, minimal patch that passes the oracle → `approve`.
- `RT2 reject-partial`: fixes the projection (line 72) but **not** the JOIN (line 78) → `reject`, feedback names the join. (The classic half-fix the oracle would expose.)
- `RT3 reject-contract-break`: drops the payment_lookup JOIN (so `payment_type_name` goes null) → `reject` on the not_null contract.
- `RT4 reject-deadcode`: a *working but ugly* patch — `COALESCE(PAYMENT_METHOD, PAYMENT_TYPE)` — passes validation but the fallback is dead code → `reject` on **quality**, asks to simplify. (Reviewer as senior engineer.)
- `RT5 confidence-routes-human`: structurally fixed but payment-lookup cardinality shifts materially → Reviewer emits low `confidence` + "recommend human review", routing to `needs_human` rather than deploy. (Trust, not syntax.)

**Orchestration (the graph) — does the loop behave?**
- `IT1 e2e-heal`: inject → run graph → `verify` green **and the highest-grossing-day oracle returns a valid row**; `git diff` of the staging SQL shows only `payment_type → payment_method`.
- `IT2 converge`: force a bad first Fixer patch → graph loops `review → fix` and converges within `max_attempts`.
- `IT3 give-up`: low `max_attempts` + unsolvable break → ends `needs_human`, never deploys.
- `IT4 no-silent-corruption`: a patch that makes staging compile but breaks the `reports.trips_report` not_null checks (or returns a null oracle) → `verify` (full `--downstream` + oracle) catches it; status ≠ healed. **The trust story, asserted as a test.**

---

## Expansion: 2 agents → N (mapped onto your `weave_mcp_hello` primitives)

`weave_mcp_hello/server.py` already demonstrates the three MCP primitives Weave traces: `@mcp.tool()` (actions), `@mcp.resource()` (shared state), `@mcp.prompt()` (reusable agent instructions). Every *new* agent is just a LangGraph node that consumes these — so going 2 → 6 agents is additive, no re-plumbing:

- **Regression Guard** *(highest-value next)* — sits between Reviewer-approve and deploy; runs the fix against `scripts/golden/` and vetoes anything that passes the new case but changes old rows. Golden manifest exposed as `@mcp.resource("golden://reports")`.
- **Historian** — after deploy, `git commit`s the patch with a trailer (diagnosis + agent + scenario) and exposes the audit log as `@mcp.resource("audit://log")` so any agent can read "who changed what, why."
- **Supervisor / Router** — replaces the hard-coded edges with an LLM router that picks the next agent; on a *downstream* breakage it walks the Historian log and `git bisect`s + reverts (the original "money demo" stretch).
- **Profiler** — split today's Diagnoser into Profiler (classifies the break: rename? drop? type-change? — structured diagnosis only) + Fixer (proposes SQL). Cleaner traces; new breakage types drop in without touching the Fixer.
- **Schema-Contract Sentinel** — holds the expected upstream schema as `@mcp.resource("schema://expected")` and detects drift **proactively** (diff live `inspect_schema` vs expected) instead of waiting for a red run.

**Through-line:** the **MCP server is the shared tool bus** for all agents (exactly what the hello-world proves at toy scale); agent instructions become `@mcp.prompt()` templates; cross-agent state (golden, audit, schema) becomes `@mcp.resource()`. Every hop stays in one Weave trace.

---

## Risks & cuts

- **Risk — re-running ingestion is slow/flaky:** mitigated by the `ALTER TABLE` inject + downstream-only re-runs (no S3 re-download).
- **Risk — Python 3.14 wheel gaps** for weave/langgraph: use a 3.11/3.12 venv for the agent code (bruin assets keep their own `python:3.11` container).
- **Risk — agent doesn't converge / over-edits:** bound the loop (`max_attempts=3`), keep the Reviewer's rubric tight, and rely on bruin's column checks as the hard pass/fail gate.
- **Cut without mercy:** a web UI, real BigQuery, the Regression-Guard/git-Historian/rollback agents (kept as a "where this goes next" slide, not built).
- **Scope decision (drift types):** build & demo the **column-rename scenario only**. The four-tool surface stays drift-agnostic, so add/drop/type-change/semantic drift are *designed-for but not built* — the story is "one new `DriftScenario`, no new tools." Do **not** spend hackathon time building a second breakage type (a type-change scenario is a post-MVP stretch at most).

## Appendix — handoff prompts (paste into a fresh Claude Code chat)

> Recommended flow: **this chat writes the Foundation (incl. the merge gate) and commits to `main`, then implements Part A.** In parallel, open one new chat for **Part B** (and optionally **Part C**). Each chat works on its **own branch/worktree**, edits **only its files**, never edits `agents/contracts.py`, and must pass `scripts/check_merge.sh` before declaring done.

**Part B — Agents, Orchestration & Weave:**
```
Implement Part B of the Self-Healing ELT build in /Users/athens/vscode101/engine-hack-1.

1. Read PLAN.md in full — especially Observability › Axis 2, Negotiation beats, Build timeline,
   Test cases, Parallelization › Part B, and Integration & merge gate.
2. Confirm agents/contracts.py exists on main (the FROZEN seam). If missing, STOP and tell me —
   Part B depends on it. Never create or edit contracts.py yourself.
3. Work on a new branch ws-b-agents (use a git worktree if you can). Edit ONLY:
   agents/tools.py, agents/graph.py, agents/prompts.py, agents/run_demo.py, tests/test_agents.py
   (stretch: agents/bruin_mcp_server.py, agents/mcp_client.py).
   Do NOT touch scripts/, zoomcamp/, duckdb.db, or agents/contracts.py.
4. Build: Diagnoser/Fixer/Reviewer as LangGraph nodes emitting the typed milestone objects from
   contracts.py (Diagnosis, TestSpec, FixCandidate, ReviewVerdict, VerifyResult, Authorization),
   each a named @weave.op; loop diagnose→fix→review→deploy→verify with review→fix on reject +
   max_attempts; tools as DirectGateway @weave.op functions (run_bruin, inspect_schema, oracle,
   apply_patch). No MCP for the MVP. Model: claude-sonnet-4-6.
5. Develop against the FakeGateway from contracts.py. Do NOT run bruin or touch the real DB.
6. DoD: against FakeGateway, `python agents/run_demo.py` converges to approve and prints a patched
   SQL string; the Weave trace shows the named milestones + a reject→re-fix + the Reviewer score;
   `pytest tests/test_agents.py -m "not llm"` green; `bash scripts/check_merge.sh` passes steps 1–4.
7. Commit to ws-b-agents. Do NOT merge — I integrate.
```

**Part A — Data & Pipeline** (only if a different chat owns A):
```
Implement Part A of the Self-Healing ELT build in /Users/athens/vscode101/engine-hack-1.

1. Read PLAN.md — especially The breakage scenario, Done heuristic (oracle), Verification,
   Parallelization › Part A, and Integration & merge gate.
2. Use the DriftScenario + oracle SQL constants from agents/contracts.py (frozen — do not edit it).
3. Branch ws-a-data (or worktree). Edit ONLY: scripts/seed.sh, scripts/inject_drift.sql,
   scripts/reset.sh, scripts/golden/ (+ capture helper), tests/test_scenario.py.
   Do NOT touch agents/ or zoomcamp/ source. You own duckdb.db during dev.
4. Build: inject_drift.sql (ALTER TABLE ingestion.trips RENAME COLUMN payment_type TO
   payment_method;), reset.sh (git checkout staging SQL + rename column back + rebuild downstream,
   idempotent), seed.sh (only if the DB needs seeding), capture the golden reports.trips_report
   snapshot, and tests/test_scenario.py asserting: after inject, `bruin run <staging> --downstream`
   fails with stderr containing "payment_type"; after reset it's green; the oracle returns a valid
   (date, gross>0) row on healthy data.
5. DoD: seed→inject→red→reset→green by hand, repeatably; `pytest tests/test_scenario.py` green;
   scripts/golden/expected_report.csv committed; `bash scripts/check_merge.sh` passes steps 1–2,4.
6. Commit to ws-a-data. Do NOT merge.
```

**Part C — Observability polish & Demo** (optional):
```
Implement Part C of the Self-Healing ELT build in /Users/athens/vscode101/engine-hack-1.

1. Read PLAN.md — especially Observability, Negotiation beats, Parallelization › Part C.
2. Branch ws-c-demo. Edit ONLY: agents/README.md, docs/demo-run-of-show.md, scripts/weave_smoke.py.
   Do NOT edit Part A/B code or agents/contracts.py.
3. Build: scripts/weave_smoke.py (a weave_mcp_hello-style check that
   weave.init("afitzc-mit/self-healing-elt") + a trivial @weave.op logs under the W&B entity); a
   one-screen README runbook; docs/demo-run-of-show.md scripting the 6 negotiation beats and what to
   point at in the Weave trace.
4. DoD: weave_smoke logs a trace; a rehearsed ≤3-min run-of-show exists.
5. Commit to ws-c-demo. Do NOT merge.
```

---

> **HTML view:** this plan is rendered to `hackathon-plan-now-valiant-book.html` (same folder) for easy reading; re-running the render overwrites it after edits.
