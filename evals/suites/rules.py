"""Rule-engine suite: the golden cases and source-quote checks (tests/rules), as a pass rate."""

from __future__ import annotations

import re
import subprocess
from typing import Any

from suites.common import ROOT


def run() -> dict[str, Any]:
    proc = subprocess.run(
        ["uv", "run", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", "tests/rules"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", tail)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", tail)) else 0
    return {
        "passed": passed,
        "failed": failed,
        "pass_rate": round(100 * passed / max(1, passed + failed), 1),
        "summary": tail,
    }
