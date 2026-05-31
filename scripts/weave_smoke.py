#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import datetime, timezone

import weave

DEFAULT_PROJECT = "afitzc-mit/self-healing-elt"
PROJECT = os.environ.get("WEAVE_PROJECT", DEFAULT_PROJECT)


@weave.op
def part_c_smoke_check(project: str) -> dict:
    return {
        "ok": True,
        "project": project,
        "check": "part-c-weave-smoke",
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    weave.init(PROJECT)
    result = part_c_smoke_check(PROJECT)
    print("=== Self-Healing ELT · Weave smoke ===")
    print(f"ok:      {result['ok']}")
    print(f"project: {result['project']}")
    print(f"check:   {result['check']}")
    print(f"trace:   https://wandb.ai/{PROJECT}")


if __name__ == "__main__":
    main()
