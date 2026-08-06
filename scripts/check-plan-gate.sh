#!/usr/bin/env bash
set -euo pipefail

PLAN_ROOT="${PLAN_ROOT:-.}" python3 - "$@" <<'PY'
"""check-plan-gate — the mechanical judge of openspec/PLAN.md.

Exits 0 only when every active change is bound to an approved plan entry whose
approval hash still matches its contract fields. Otherwise names the change and
exactly one reason: NO-ENTRY, NOT-APPROVED, HASH-MISMATCH, STALE.
"""
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("PLAN_ROOT", ".")).resolve()
PLAN = ROOT / "openspec" / "PLAN.md"
CHANGES = ROOT / "openspec" / "changes"

FIELDS = ("Change", "Depends on", "Done when", "State",
          "Approved by", "Approved at", "Approval hash")
# A field left unset is written as an em dash (or a dash, or left blank). Any of
# those means "no value", never a literal to be hashed or trusted.
EMPTY = {"", "-", "—", "--"}

ENTRY_RE = re.compile(r"^###\s+(E\d+)\s+—\s+(.*?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*(?P<key>[^*]+)\*\*:\s*(?P<val>.*)$")


def norm(value):
    """Strip, then collapse internal whitespace runs to a single space."""
    return re.sub(r"\s+", " ", (value or "").strip())


def parse_plan(text):
    """Return [{id, title, fields{}}] in file order.

    A field value may continue on following indented lines; those are folded in
    so that a two-line `Done when` hashes the same as its one-line equivalent.
    """
    entries = []
    current = None
    pending_key = None
    for raw in text.splitlines():
        m = ENTRY_RE.match(raw)
        if m:
            current = {"id": m.group(1), "title": norm(m.group(2)), "fields": {}}
            entries.append(current)
            pending_key = None
            continue
        if current is None:
            continue
        m = FIELD_RE.match(raw)
        if m:
            pending_key = m.group("key").strip()
            current["fields"][pending_key] = m.group("val").strip()
            continue
        # continuation line of the field above: indented, non-empty, not a new item
        if pending_key and raw.startswith((" ", "\t")) and raw.strip():
            current["fields"][pending_key] += " " + raw.strip()
            continue
        if not raw.strip():
            pending_key = None
    return entries


def field(entry, key):
    return entry["fields"].get(key, "").strip()


def is_set(value):
    return value.strip() not in EMPTY


def canonical(entry):
    """The approved contract: title, Change, Depends on, Done when.

    State is deliberately excluded — ordinary progress must never invalidate an
    approval.
    """
    return "\n".join(norm(v) for v in (
        entry["title"],
        field(entry, "Change"),
        field(entry, "Depends on"),
        field(entry, "Done when"),
    ))


def entry_hash(entry):
    digest = hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()
    return "sha256:" + digest[:16]


def approved(entry):
    """All three parts present, and the hash matches the current contract."""
    if not is_set(field(entry, "Approved by")):
        return False, "NOT-APPROVED"
    if not is_set(field(entry, "Approved at")):
        return False, "NOT-APPROVED"
    declared = field(entry, "Approval hash")
    if not declared.startswith("sha256:"):
        return False, "NOT-APPROVED"
    if declared.split()[0] != entry_hash(entry):
        return False, "HASH-MISMATCH"
    return True, None


def active_changes():
    if not CHANGES.is_dir():
        return []
    return sorted(d.name for d in CHANGES.iterdir()
                  if d.is_dir() and d.name != "archive")


def archived_changes():
    """Map change name -> archive directory name, from `archive/<date>-<name>`."""
    archive = CHANGES / "archive"
    found = {}
    if not archive.is_dir():
        return found
    for d in sorted(archive.iterdir()):
        if not d.is_dir():
            continue
        m = re.match(r"^\d{4}-\d{2}-\d{2}-(?P<name>.+)$", d.name)
        found[m.group("name") if m else d.name] = d.name
    return found


def fail(lines):
    print("check-plan-gate: FAILED\n")
    for line in lines:
        print(line)
    print("\nFix: see the reason table in rules/plan-mode.md.")
    sys.exit(1)


def main(argv):
    only_change = None
    if argv and argv[0] == "--hash":
        if len(argv) < 2:
            print("usage: check-plan-gate.sh --hash <entry-id>", file=sys.stderr)
            return 2
        if not PLAN.is_file():
            print(f"check-plan-gate: no plan at {PLAN}", file=sys.stderr)
            return 1
        wanted = argv[1].strip()
        for entry in parse_plan(PLAN.read_text(encoding="utf-8")):
            if entry["id"] == wanted:
                print(entry_hash(entry))
                return 0
        print(f"check-plan-gate: no entry {wanted} in {PLAN}", file=sys.stderr)
        return 1
    if argv and argv[0] == "--change":
        if len(argv) < 2:
            print("usage: check-plan-gate.sh --change <name>", file=sys.stderr)
            return 2
        only_change = argv[1].strip()
    elif argv:
        print(f"check-plan-gate: unknown argument {argv[0]}", file=sys.stderr)
        return 2

    # A missing plan is a failure, not a vacuous pass: active changes with no
    # plan at all is exactly what this gate exists to catch.
    if not PLAN.is_file():
        targets = [only_change] if only_change else active_changes()
        if not targets:
            print(f"check-plan-gate: no plan at {PLAN}, and no active change — "
                  "nothing to gate yet.")
            return 0
        fail([f"No plan file at {PLAN}.",
              "Active change(s) with no plan: " + ", ".join(targets)])

    entries = parse_plan(PLAN.read_text(encoding="utf-8"))
    by_change = {}
    for entry in entries:
        change = field(entry, "Change")
        if is_set(change) and change != "none":
            by_change.setdefault(change, []).append(entry)

    problems = []

    changes = [only_change] if only_change else active_changes()
    for change in changes:
        bound = by_change.get(change)
        if not bound:
            problems.append(f"  {change}: NO-ENTRY — no plan entry declares "
                            f"`Change: {change}`")
            continue
        entry = bound[0]
        ok, reason = approved(entry)
        if not ok:
            detail = (f"expected {entry_hash(entry)}, found "
                      f"{field(entry, 'Approval hash') or '(none)'}"
                      if reason == "HASH-MISMATCH"
                      else "no valid approval block")
            problems.append(f"  {change}: {reason} — entry {entry['id']} "
                            f"({detail})")

    # STALE is a property of the plan, not of an active change: an archived
    # change whose entry never advanced. Skipped when checking one change.
    if not only_change:
        archived = archived_changes()
        for change, entry_list in by_change.items():
            if change not in archived:
                continue
            entry = entry_list[0]
            state = field(entry, "State").lower()
            if state != "done":
                problems.append(
                    f"  {change}: STALE — archived as "
                    f"{archived[change]}, but entry {entry['id']} is "
                    f"'{state or 'unset'}', not 'done'")

    if problems:
        fail(problems)

    scope = f"change '{only_change}'" if only_change else \
        f"{len(changes)} active change(s)"
    print(f"check-plan-gate: PASSED ({scope}, {len(entries)} plan entries)")
    return 0


sys.exit(main(sys.argv[1:]))
PY
