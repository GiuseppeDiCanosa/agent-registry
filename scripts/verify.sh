#!/usr/bin/env bash
set -euo pipefail

echo "=== Spec Verification ==="
echo ""

bash scripts/check-spec-links.sh
bash scripts/check-target-ownership.sh
python3 scripts/build-spec-manifest.py

echo ""
echo "Spec checks passed. Running test suite..."
echo ""

if [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || [ -f "setup.cfg" ]; then
    pytest tests/ -v
elif [ -f "package.json" ] && grep -q '"test"' package.json; then
    npm test
elif [ -f "Cargo.toml" ]; then
    cargo test
elif ! find openspec/specs -mindepth 1 -name 'spec.md' -print -quit 2>/dev/null | grep -q .; then
    echo "No test runner detected, but no specs exist yet either — this is a fresh bootstrap, not a failure."
    echo "Baseline: spec checks passed, test suite SKIPPED (no specs/tests yet)."
else
    echo "No test runner detected. Run your test suite manually."
    exit 1
fi
