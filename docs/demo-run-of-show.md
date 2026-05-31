# Self-Healing ELT Demo Run-of-Show

A rehearsed ≤3-minute script for presenting the six negotiation beats in the Weave trace.

## Setup before speaking

- Run `python scripts/weave_smoke.py` and confirm `part_c_smoke_check` appears in Weave.
- Run `bash scripts/reset.sh` so the pipeline starts green.
- Have `https://wandb.ai/afitzc-mit/self-healing-elt` open in the trace view.
- Keep a terminal ready for `python -m agents.run_demo`.

## 0:00-0:25 · Frame the incident

Say: "This demo starts with a healthy Bruin/DuckDB ELT pipeline. We inject one realistic upstream drift: `payment_type` becomes `payment_method`. The agents must diagnose, negotiate, patch, and prove the business report still works."

Point at:

- Terminal command: `python -m agents.run_demo`.
- Weave project: `afitzc-mit/self-healing-elt`.
- Trace root: `run_repair` or the top LangGraph run.

## 0:25-0:55 · Beat 1: correctness reject

Say: "The first proposal is intentionally not trusted blindly. The reviewer catches the important failure mode: a partial fix that still leaves a `PAYMENT_TYPE` reference in the join."

Point at:

- `diagnose`: red `run_bruin` output containing `PAYMENT_TYPE` not found.
- `inspect_schema`: upstream has `payment_method`, not `payment_type`.
- First `fix`: `FixCandidate.attempt_n == 1`.
- First `review`: `decision == reject`, with correctness findings naming the remaining join/reference.

## 0:55-1:20 · Beat 2: quality reject

Say: "Passing SQL is not enough. The reviewer is acting like a senior engineer, not a rubber stamp: if the fix uses a dead fallback such as `COALESCE(PAYMENT_METHOD, PAYMENT_TYPE)`, it rejects the quality even if validation looks tempting."

Point at:

- `review.quality_findings` if the live trace includes the quality rejection.
- If not live in the current Part B build, call it out as the planned/rehearsed quality beat from the reviewer rubric.

## 1:20-1:45 · Beat 3: behavioural proof

Say: "The final check is behavioural. We do not stop at 'the SQL compiled'; we ask the business question again: what was the highest-grossing day?"

Point at:

- `verify`: `bruin_status == green`, `oracle_status == green`, `healed == true`.
- `oracle`: row like `2022-01-21 | 187341.22`.
- Terminal summary: `verify: bruin=green oracle=green healed=True`.

## 1:45-2:10 · Beat 4: risk and confidence

Say: "The reviewer also emits confidence. That gives us a trust signal: deploy automatically when confidence is high, route to a human when the blast radius or data shift is suspicious."

Point at:

- `review.confidence`.
- `review.rationale`.
- Any correctness or quality findings that explain the confidence.

## 2:10-2:30 · Beat 5: cross-check

Say: "The stretch version adds a back-check: if someone approves before the oracle ran, the diagnosing side can challenge the approval. That is agents holding each other accountable, not a one-way pipeline."

Point at:

- If implemented: the back-edge from `review` to `diagnose` or the withheld approval span.
- If not implemented: explicitly mark this as the stretch beat and move on quickly.

## 2:30-2:55 · Beat 6: deploy authorisation

Say: "The trace separates authorisation from execution. First we see an explicit approval with evidence and an audit id; only then does the write happen."

Point at:

- `deploy`: `Authorization(decision="APPROVE", audit_id=...)`.
- `apply_patch`: the single mutation span.
- `verify`: final proof after deployment.

## 2:55-3:00 · Close

Say: "The point is not that an agent changed SQL. The point is that the trace shows professional collaboration: evidence, disagreement, review, authorisation, and behavioural proof."

Point at:

- The full trace timeline: `diagnose → fix → review → fix → review → deploy → verify`.
- Optional terminal receipt: `git diff zoomcamp/pipeline/assets/staging/trips.sql` before reset, showing only the intended column rename fix.
