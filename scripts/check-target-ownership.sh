#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path
import fnmatch
import subprocess
import sys

ROOT = Path.cwd()
SPEC_ROOT = ROOT / "openspec" / "specs"


def run(cmd):
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def changed_files():
    # Prefer PR/main comparison when origin/main is available.
    code, out, _ = run("git rev-parse --verify origin/main")
    if code == 0:
        code, out, _ = run("git diff --name-only origin/main...HEAD")
        if out:
            return set(out.splitlines())

    # Fallback for local demos: staged + unstaged files.
    files = set()
    for cmd in ["git diff --name-only --cached", "git diff --name-only"]:
        _, out, _ = run(cmd)
        if out:
            files.update(out.splitlines())
    return files


def parse_targets(spec: Path):
    text = spec.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return []

    parts = text.split("---", 2)
    if len(parts) < 3:
        return []

    frontmatter = parts[1].splitlines()
    targets = []
    in_targets = False

    for line in frontmatter:
        stripped = line.strip()
        if stripped.startswith("targets:"):
            in_targets = True
            continue
        if in_targets:
            if stripped.startswith("-"):
                raw = stripped[1:].strip().strip('"\'')
                # targets: paths are project-root-relative, not relative to the spec file.
                resolved = (ROOT / raw).resolve()
                try:
                    rel = resolved.relative_to(ROOT)
                    targets.append(str(rel))
                except ValueError:
                    targets.append(raw)
            elif stripped and not line.startswith(" "):
                in_targets = False

    return targets


owners = {}
for spec in sorted(SPEC_ROOT.glob("**/spec.md")):
    for target in parse_targets(spec):
        owners.setdefault(target, []).append(str(spec.relative_to(ROOT)))

changed = changed_files()
changed_specs = {f for f in changed if f.startswith("openspec/specs/") and f.endswith("spec.md")}
violations = []

for changed_file in changed:
    for target_pattern, spec_owners in owners.items():
        if changed_file == target_pattern or fnmatch.fnmatch(changed_file, target_pattern):
            if not any(owner in changed_specs for owner in spec_owners):
                violations.append((changed_file, spec_owners))

if violations:
    print("FAILED — target file changed without spec update.\n")
    for file, spec_owners in violations:
        print("Changed target:")
        print(f"  {file}\n")
        print("Owned by:")
        for owner in spec_owners:
            print(f"  {owner}")
        print()
    print("Fix:")
    print("  1. update the owning spec first")
    print("  2. ask the agent to update targets and tests from the approved spec")
    sys.exit(1)

print("check-target-ownership: PASSED")
PY
