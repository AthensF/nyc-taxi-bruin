"""DirectGateway — the real ToolGateway over bruin + DuckDB.

Only the *signal* operations are traced (`run_bruin`, `inspect_schema`, `oracle`);
generic `query_duckdb` stays an internal helper; `apply_patch` is the single gated
mutation (called only at the deploy node). `run_sql` is a demo-harness helper for
inject/reset and is NOT part of the agent capability surface.

Part B develops against contracts.FakeGateway; this is swapped in at integration.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from agents.contracts import (
    DUCKDB_PATH,
    ORACLE_SQL,
    REPO_ROOT,
    Column,
    ToolResult,
)
from agents.trace import op


class DirectGateway:
    def __init__(self, duckdb_path: Path = DUCKDB_PATH, repo_root: Path = REPO_ROOT):
        self.duckdb_path = Path(duckdb_path)
        self.repo_root = Path(repo_root)

    @op
    def run_bruin(self, asset_path: str, downstream: bool = True, command: str = "run") -> ToolResult:
        cmd = ["bruin", command, asset_path]
        if downstream and command == "run":
            cmd.append("--downstream")
        proc = subprocess.run(
            cmd, cwd=str(self.repo_root), capture_output=True, text=True, timeout=600
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }

    @op
    def inspect_schema(self, table: str) -> list[Column]:
        rows = self._read_sql(f"DESCRIBE {table};")
        return [{"column": str(r[0]), "type": str(r[1])} for r in rows]

    def query_duckdb(self, sql: str) -> list[dict]:  # internal helper (not a traced signal)
        import duckdb

        con = duckdb.connect(str(self.duckdb_path), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()

    @op
    def oracle(self) -> Optional[dict]:
        rows = self.query_duckdb(ORACLE_SQL)
        return rows[0] if rows else None

    @op
    def apply_patch(self, path: str, content: str) -> ToolResult:
        Path(path).write_text(content)
        return {"ok": True, "stdout": f"wrote {len(content)} bytes to {path}\n",
                "stderr": "", "exit_code": 0}

    # --- demo-harness helpers (not part of ToolGateway) --- #
    def run_sql(self, sql: str) -> None:
        """Execute DDL/DML (inject/reset). Read-write connection."""
        import duckdb

        con = duckdb.connect(str(self.duckdb_path))
        try:
            con.execute(sql)
        finally:
            con.close()

    def _read_sql(self, sql: str):
        import duckdb

        con = duckdb.connect(str(self.duckdb_path), read_only=True)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()
