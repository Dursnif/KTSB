#!/usr/bin/env python3
"""
scripts/run_tests.py — Run pytest tests/ and output structured JSON.

Called by /api/run_tests endpoint via kaare venv python.
Usage: PYTHONPATH=/kaare /kaare/venv/bin/python /kaare/scripts/run_tests.py
"""
import json
import subprocess
import sys
import re


result = subprocess.run(
    [sys.executable, "-m", "pytest", "/kaare/tests", "-v", "--tb=line", "--no-header", "-p", "no:warnings",
     "--confcutdir=/kaare/tests"],
    capture_output=True,
    text=True,
    timeout=120,
    env={"PYTHONPATH": "/kaare", "HOME": "/root", "PATH": "/usr/bin:/bin"},
)

output = result.stdout + result.stderr
passed_names = re.findall(r"tests/\S+::(\S+) PASSED", output)
failed_blocks = re.findall(r"tests/\S+::(\S+) FAILED", output)
error_blocks  = re.findall(r"tests/\S+::(\S+) ERROR", output)

failures = []
for name in failed_blocks + error_blocks:
    detail_match = re.search(rf"{re.escape(name)}.*?\nE\s+(.+)", output)
    detail = detail_match.group(1).strip() if detail_match else ""
    failures.append({"name": name, "detail": detail})

total = len(passed_names) + len(failures)
ok = len(failures) == 0 and total > 0

print(json.dumps({
    "ok": ok,
    "passed": len(passed_names),
    "failed": len(failures),
    "total": total,
    "failures": failures,
}))
