from pathlib import Path
import json
import re

ROOT = Path.cwd()
SPEC_ROOT = ROOT / "openspec" / "specs"
REQ_PATTERN = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$", re.MULTILINE)
TEST_PATTERN = re.compile(r"\[@test\]\s+([^\s`)]+)")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


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
                    targets.append(str(resolved.relative_to(ROOT)))
                except ValueError:
                    targets.append(raw)
            elif stripped and not line.startswith(" "):
                in_targets = False

    return targets


def parse_requirements(spec: Path):
    text = spec.read_text(encoding="utf-8")
    req_matches = list(REQ_PATTERN.finditer(text))
    requirements = {}

    for idx, match in enumerate(req_matches):
        req_name = match.group(1)
        req_key = slugify(req_name)
        start = match.end()
        end = req_matches[idx + 1].start() if idx + 1 < len(req_matches) else len(text)
        block = text[start:end]
        tests = []
        for raw in TEST_PATTERN.findall(block):
            # [@test] paths are project-root-relative, not relative to the spec file.
            resolved = (ROOT / raw).resolve()
            try:
                tests.append(str(resolved.relative_to(ROOT)))
            except ValueError:
                tests.append(raw)
        requirements[req_key] = {"name": req_name, "tests": tests}

    return requirements


manifest = {}
for spec in sorted(SPEC_ROOT.glob("**/spec.md")):
    manifest[str(spec.relative_to(ROOT))] = {
        "targets": parse_targets(spec),
        "requirements": parse_requirements(spec),
    }

output = ROOT / ".spec-source-manifest.json"
output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"spec manifest written to {output.relative_to(ROOT)}")
