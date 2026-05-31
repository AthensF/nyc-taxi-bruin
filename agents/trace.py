"""Weave tracing shim — prefer real Weave, degrade to a no-op if unavailable.

This lets the orchestration code (graph.py) and tests import and run without
`weave` installed, while the live demo (run_demo.py) gets full tracing. Use
`@op` on anything you want as a named span; call `init(project)` once at startup.
"""
from __future__ import annotations

try:
    import weave as _weave

    def init(project: str | None = None):
        import os
        if not os.environ.get("WANDB_API_KEY"):
            return None  # no Weave login -> run untraced instead of hanging on a prompt
        try:
            return _weave.init(project)
        except Exception:
            return None

    op = _weave.op

except Exception:  # weave not installed → no-op decorator
    def init(project: str | None = None):
        return None

    def op(fn=None, *args, **kwargs):
        if callable(fn):
            return fn

        def _deco(f):
            return f

        return _deco
