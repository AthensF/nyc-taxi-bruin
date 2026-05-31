# engine-hack-1 — Self-Healing ELT (hackathon)

**Start here →** the full design plan is in **[`PLAN.md`](./PLAN.md)**. Read it first.

## What's in this repo
- **[`PLAN.md`](./PLAN.md)** — the design plan: three goal-driven agents (**Diagnoser → Fixer → Reviewer**) repair an upstream schema-drift breakage in the bruin pipeline, with the negotiation observable in **WandB Weave**.
- `zoomcamp/` — the working bruin ELT pipeline (`nyc-taxi-pipeline`, local DuckDB) that gets broken and healed.
- `weave_mcp_hello/` — a working Weave↔MCP tracing PoC; the agent tool layer is seeded from this.
- `self_healing_etl/` — an earlier CSV-based prototype of the same idea (5-agent variant).

## For another agent picking this up
Read `PLAN.md`, then find your lane in its **"Parallelization — workstreams"** section
(A: data/pipeline · B: agents/orchestration · C: observability/demo) and honor the
`agents/contracts.py` seam. The **done heuristic** for the whole demo is that the healed
pipeline can still answer *"what is the highest-grossing day during the month?"*

## Run the demo

1. `cp .env.example .env` and paste your `ANTHROPIC_API_KEY` (and optionally `WANDB_API_KEY` for the Weave trace).
2. `./demo.sh`  — first run builds `.venv`; it injects drift, the agents heal it, verifies, and resets.
