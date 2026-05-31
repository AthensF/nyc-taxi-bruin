"""FROZEN SEAM — shared contract between Part A (data/pipeline) and Part B (agents/orchestration).

Rules (see PLAN.md › Parallelization):
  * This file is written ONCE in the Foundation step and committed to `main`.
  * After that it is FROZEN. No workstream edits it unilaterally — a needed change
    goes through the human integrator and is re-shared.
  * Both A and B import ONLY from here for anything they share, so the parts fit
    back together at integration.

Contents:
  - Paths / config constants
  - ToolResult / Column            (tool I/O)
  - Milestone objects              (the Axis-2 handoff artifacts surfaced in Weave)
  - RepairState                    (the LangGraph blackboard)
  - ToolGateway (Protocol)         (capability surface the agents use)
  - DriftScenario + registry       (the break, owned conceptually by A)
  - ORACLE_SQL                     (the business done-heuristic)
  - FakeGateway                    (canned, faithful stand-in so B builds before A lands)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, TypedDict

try:  # NotRequired is in typing on 3.11+, fall back for older envs
    from typing import NotRequired
except ImportError:  # pragma: no cover
    from typing_extensions import NotRequired


# --------------------------------------------------------------------------- #
# Paths / config                                                              #
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
DUCKDB_PATH = REPO_ROOT / "duckdb.db"
PIPELINE_PATH = REPO_ROOT / "zoomcamp" / "pipeline" / "pipeline.yml"
STAGING_ASSET_PATH = REPO_ROOT / "zoomcamp" / "pipeline" / "assets" / "staging" / "trips.sql"
REPORTS_ASSET_PATH = REPO_ROOT / "zoomcamp" / "pipeline" / "assets" / "reports" / "trips_report.sql"

WEAVE_PROJECT = os.environ.get("WEAVE_PROJECT", "afitzc-mit/self-healing-elt")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))

# The business done-heuristic: "what is the highest-grossing day during the month?"
ORACLE_SQL = (
    "SELECT pickup_date, SUM(total_amount) AS gross_revenue\n"
    "FROM reports.trips_report\n"
    "GROUP BY pickup_date\n"
    "ORDER BY gross_revenue DESC\n"
    "LIMIT 1;"
)


# --------------------------------------------------------------------------- #
# Tool I/O                                                                     #
# --------------------------------------------------------------------------- #
class ToolResult(TypedDict):
    ok: bool
    stdout: str
    stderr: str
    exit_code: int


class Column(TypedDict):
    column: str
    type: str


# --------------------------------------------------------------------------- #
# Milestone objects — the handoff artifacts exposed as Axis-2 Weave spans.     #
# These are what make the trace read like an incident thread, not chain-of-    #
# thought. Keep them plain dataclasses (Weave serializes them cleanly).        #
# --------------------------------------------------------------------------- #
@dataclass
class Diagnosis:
    """The incident report (Diagnoser → Fixer)."""
    root_cause: str
    why: str
    evidence: list = field(default_factory=list)        # [error_excerpt, schema_diff, ...]
    references: list = field(default_factory=list)       # ["staging/trips.sql:72", ...]
    acceptance_criteria: list = field(default_factory=list)


@dataclass
class TestSpec:
    """The test the Fixer must pass (proposed by the Diagnoser)."""
    oracle_sql: str = ORACLE_SQL
    expected_shape: str = "one row: (pickup_date, gross_revenue>0) within the loaded month"
    structural_assertions: list = field(default_factory=list)


@dataclass
class FixCandidate:
    """The PR description (Fixer → Reviewer). `sql` is the full proposed file content."""
    attempt_n: int
    sql: str
    summary: str = ""
    files_changed: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    blast_radius: str = "low"
    criteria_addressed: dict = field(default_factory=dict)   # {"no payment_type refs": True, ...}


@dataclass
class ReviewVerdict:
    """The senior-engineer review (Reviewer → loop/deploy)."""
    decision: str                       # "approve" | "reject"
    confidence: float = 0.0
    rationale: str = ""
    correctness_findings: list = field(default_factory=list)
    quality_findings: list = field(default_factory=list)


@dataclass
class VerifyResult:
    """The behavioral proof (verify node)."""
    bruin_status: str                   # "green" | "red"
    oracle_status: str                  # "green" | "red"
    healed: bool
    oracle_answer: Optional[dict] = None


@dataclass
class Authorization:
    """Proposal→execution split at deploy."""
    decision: str                       # "APPROVE"
    audit_id: str
    evidence: list = field(default_factory=list)


@dataclass
class QualityScore:
    """Post-deploy quality/maintainability scorecard of the shipped SQL (non-gating)."""
    overall: int                        # 1-10
    rationale: str = ""
    criteria: dict = field(default_factory=dict)   # {criterion: {"score": int, "note": str}}


# --------------------------------------------------------------------------- #
# Graph state — the LangGraph blackboard                                       #
# --------------------------------------------------------------------------- #
class RepairState(TypedDict):
    # inputs (set by run_demo)
    staging_asset_path: str
    upstream_table: str
    scenario: str
    max_attempts: int

    # milestones (agents write)
    attempts: NotRequired[int]
    diagnosis: NotRequired[Diagnosis]
    test_spec: NotRequired[TestSpec]
    fix: NotRequired[FixCandidate]
    review: NotRequired[ReviewVerdict]
    verify: NotRequired[VerifyResult]
    authorization: NotRequired[Authorization]
    quality: NotRequired[QualityScore]

    # outcome
    final_status: NotRequired[str]      # "healed" | "needs_human" | "noop"


# --------------------------------------------------------------------------- #
# Tool gateway — capability surface. Two impls: tools.DirectGateway (Part B)   #
# and FakeGateway (below). Which methods are @weave.op / MCP-exposed is        #
# Part B's concern; this protocol is just the capability contract.            #
# --------------------------------------------------------------------------- #
class ToolGateway(Protocol):
    def run_bruin(self, asset_path: str, downstream: bool = True, command: str = "run") -> ToolResult: ...
    def inspect_schema(self, table: str) -> list[Column]: ...
    def query_duckdb(self, sql: str) -> list[dict]: ...
    def oracle(self) -> Optional[dict]: ...
    def apply_patch(self, path: str, content: str) -> ToolResult: ...


# --------------------------------------------------------------------------- #
# Drift scenarios — A owns these. The contract B's tests assert on.           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriftScenario:
    name: str
    description: str
    upstream_table: str
    staging_asset_path: str
    inject_sql: str
    reset_sql: str
    renamed_from: str
    renamed_to: str
    expected_error_substring: str       # CONTRACT: injecting must produce this in bruin stderr (case-insensitive)
    references_to_update: tuple = ()


COLUMN_RENAME = DriftScenario(
    name="column_rename",
    description="Upstream renames payment_type -> payment_method; staging JOIN breaks.",
    upstream_table="ingestion.trips",
    staging_asset_path=str(STAGING_ASSET_PATH),
    inject_sql="ALTER TABLE ingestion.trips RENAME COLUMN payment_type TO payment_method;",
    reset_sql="ALTER TABLE ingestion.trips RENAME COLUMN payment_method TO payment_type;",
    renamed_from="payment_type",
    renamed_to="payment_method",
    expected_error_substring="payment_type",
    references_to_update=(
        "staging/trips.sql:72  PAYMENT_TYPE as payment_type",
        "staging/trips.sql:78  LEFT JOIN ... ON d.PAYMENT_TYPE = p.payment_type_id",
    ),
)

SCENARIOS = {s.name: s for s in (COLUMN_RENAME,)}


# --------------------------------------------------------------------------- #
# FakeGateway — a faithful, in-memory stand-in so Part B can build the whole   #
# negotiation loop BEFORE Part A's real pipeline exists. It models the demo:   #
#   * starts broken (run_bruin red with the real error substring)             #
#   * inspect_schema shows the post-drift catalog (payment_method, no          #
#     payment_type)                                                            #
#   * once a CORRECT patch is applied (no bare payment_type, uses              #
#     payment_method) run_bruin/oracle go green                                #
#   * a half-fix that still references payment_type stays red -> drives the    #
#     reject -> re-fix loop                                                    #
# apply_patch here is in-memory ONLY (never writes the real repo file), so B   #
# dev cannot clobber staging/trips.sql.                                        #
# --------------------------------------------------------------------------- #
# Post-drift schema of ingestion.trips (payment_type renamed to payment_method)
POST_DRIFT_SCHEMA: list[Column] = [
    {"column": "VendorID", "type": "INTEGER"},
    {"column": "pickup_datetime", "type": "TIMESTAMP"},
    {"column": "dropoff_datetime", "type": "TIMESTAMP"},
    {"column": "passenger_count", "type": "DOUBLE"},
    {"column": "trip_distance", "type": "DOUBLE"},
    {"column": "RatecodeID", "type": "DOUBLE"},
    {"column": "store_and_fwd_flag", "type": "VARCHAR"},
    {"column": "PULocationID", "type": "INTEGER"},
    {"column": "DOLocationID", "type": "INTEGER"},
    {"column": "payment_method", "type": "INTEGER"},   # <- was payment_type
    {"column": "fare_amount", "type": "DOUBLE"},
    {"column": "total_amount", "type": "DOUBLE"},
    {"column": "congestion_surcharge", "type": "DOUBLE"},
    {"column": "airport_fee", "type": "DOUBLE"},
    {"column": "taxi_type", "type": "VARCHAR"},
    {"column": "extracted_at", "type": "TIMESTAMP"},
]

_FAKE_ERROR = (
    'Binder Error: Referenced column "PAYMENT_TYPE" not found in FROM clause!\n'
    "Candidate bindings: \"payment_method\"\n"
    "LINE 78:   ON d.PAYMENT_TYPE = p.payment_type_id\n"
)
_FAKE_ORACLE_ROW = {"pickup_date": "2022-01-21", "gross_revenue": 187341.22}

# bare `payment_type` NOT followed by `_name` (so payment_type_name is allowed)
# bare `payment_type` column only — NOT payment_type_name or payment_type_id (the lookup PK)
_BARE_PAYMENT_TYPE = re.compile(r"payment_type(?![_a-zA-Z])", re.IGNORECASE)


def _looks_fixed(content: Optional[str]) -> bool:
    if not content:
        return False
    return (not _BARE_PAYMENT_TYPE.search(content)) and ("payment_method" in content.lower())


class FakeGateway:
    """In-memory ToolGateway for Part B development. Implements the same protocol."""

    def __init__(self, scenario: DriftScenario = COLUMN_RENAME):
        self.scenario = scenario
        self._applied: Optional[str] = None
        self._green: bool = False

    def run_bruin(self, asset_path: str, downstream: bool = True, command: str = "run") -> ToolResult:
        self._green = _looks_fixed(self._applied)
        if self._green:
            return {"ok": True, "stdout": "Successfully validated and ran 2 assets.\n",
                    "stderr": "", "exit_code": 0}
        return {"ok": False, "stdout": "", "stderr": _FAKE_ERROR, "exit_code": 1}

    def inspect_schema(self, table: str) -> list[Column]:
        return list(POST_DRIFT_SCHEMA)

    def query_duckdb(self, sql: str) -> list[dict]:
        if self._green and "gross_revenue" in sql.lower():
            return [dict(_FAKE_ORACLE_ROW)]
        return []

    def oracle(self) -> Optional[dict]:
        rows = self.query_duckdb(ORACLE_SQL)
        return rows[0] if rows else None

    def apply_patch(self, path: str, content: str) -> ToolResult:
        # in-memory only — never writes the real repo file during dev
        self._applied = content
        return {"ok": True, "stdout": f"(fake) staged {len(content)} bytes for {path}\n",
                "stderr": "", "exit_code": 0}
