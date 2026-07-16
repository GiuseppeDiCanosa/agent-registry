#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path
import re
import sys

ROOT = Path.cwd()
spec_root = ROOT / "openspec" / "specs"
pattern = re.compile(r"\[@test\]\s+([^\s`)]+)")
missing = []

for spec in sorted(spec_root.glob("**/spec.md")):
    text = spec.read_text(encoding="utf-8")
    for raw_link in pattern.findall(text):
        # [@test] paths are project-root-relative, not relative to the spec file.
        candidate = (ROOT / raw_link).resolve()
        project_relative = candidate.relative_to(ROOT) if str(candidate).startswith(str(ROOT)) else candidate
        if not candidate.exists():
            missing.append((spec, raw_link, project_relative))

if missing:
    print("FAILED — missing tests referenced by specs.\n")
    current_spec = None
    for spec, raw_link, resolved in missing:
        if spec != current_spec:
            print(f"--- {spec}")
            current_spec = spec
        print(f"  MISSING {raw_link} -> {resolved}")
    print("\nA missing [@test] is worse than a failing test: the requirement is not verifiable.")
    sys.exit(1)

print("check-spec-links: PASSED")
PY
