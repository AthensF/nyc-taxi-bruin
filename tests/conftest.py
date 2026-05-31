"""Shared pytest config for the Self-Healing ELT build (Foundation-owned).

Registers the markers all three workstreams use so `-m "not llm"` etc. work
without warnings:
  * parity      — checks FakeGateway matches the REAL pipeline (needs duckdb + bruin)
  * integration — end-to-end heal (needs A + B + keys)
  * llm         — requires ANTHROPIC_API_KEY (real Claude calls)
"""
import sys
from pathlib import Path

# Make `import agents...` work when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_configure(config):
    for marker in (
        "parity: FakeGateway-vs-real-pipeline parity (needs duckdb + bruin)",
        "integration: end-to-end heal (needs Part A + Part B + API keys)",
        "llm: requires ANTHROPIC_API_KEY (real Claude calls)",
    ):
        config.addinivalue_line("markers", marker)
