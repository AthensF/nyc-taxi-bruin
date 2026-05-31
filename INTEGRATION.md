# Integration checklist — Self-Healing ELT

The merge gate (`bash scripts/check_merge.sh`) answers "are we ready to merge?" — not vibes.
Each chat ticks its box when its branch passes the gate. See `PLAN.md › Integration & merge gate`.

## The one rule
`agents/contracts.py` is **FROZEN**. Nobody edits it on a feature branch. A needed change
goes through the human integrator and is re-shared to all chats.

## Per-branch self-certification (no API key needed)
- [ ] **Foundation** (`main`): `bash scripts/check_merge.sh` → steps 1 & 4 green.
- [ ] **Part A** (`ws-a-data`): step 1 (contract unchanged), step 2 (`tests/test_scenario.py`),
      step 4 (parity green — real pipeline matches the fake). `scripts/golden/expected_report.csv` committed.
- [ ] **Part B** (`ws-b-agents`): step 1, step 3 (`tests/test_agents.py -m "not llm"`),
      step 4. Graph converges `approve` on `FakeGateway`; Weave trace shows the milestones + reject→re-fix + Reviewer score.
- [ ] **Part C** (`ws-c-demo`, optional): `scripts/weave_smoke.py` logs a trace; run-of-show written.

## Integration branch (human owns)
Merge order: `main (foundation) → A → B → C`.
- [ ] All of the above green.
- [ ] `bash scripts/check_merge.sh` → steps **1–5 green** (end-to-end heal with real gateway + keys).
- [ ] Full `PLAN.md › Verification` section passes (oracle returns a valid highest-grossing day; `git diff` shows only `payment_type → payment_method`).

If a parity test (step 4) fails, the fix is in **Part A's pipeline** or the **Foundation's `FakeGateway` fixtures** — never a silent edit to a downstream consumer.
